"""Asking the user's leave: a bot asks for a folder, the network or a go-ahead; the user allows it or not; what's
allowed is the task's from its next turn, and some things can't be asked for at all."""

import pytest
from fakes import ScriptedCrew, bot, wait_until
from test_bot_api import ADMIN, as_bot, crew_app, start  # noqa: F401
from test_sessions import SessionBackend, make, settled

from crewbots import paths
from crewbots.backends import Access, RunSpec
from crewbots.backends.claude_cli import settings
from crewbots.backends.codex_cli import sandbox_overrides
from crewbots.backends.policy import READ_ONLY, Policy
from crewbots.crew import CrewError
from crewbots.events import Done
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def shown(store, task_id, kind="permission"):
    return [e.data for e in store.events(task_id) if e.kind == kind]


def crewbots_messages(store, task_id):
    return [e.data["text"] for e in store.events(task_id) if e.kind == "message" and e.data["from"] == "crewbots"]


async def test_a_thread_asks_and_the_chat_it_came_from_shows_it_too(store, tmp_path):
    renders = paths.home() / "Movies" / "Renders"
    renders.mkdir(parents=True)
    crew = ScriptedCrew(store, tmp_path / "crew", bot("chief", can_delegate=True),
                        bot("engineer", access=Access.WORKSPACE_WRITE))
    state = {}

    def chief(spec, me):
        if spec.prompt == "go":
            state["thread"] = crew.runner.delegate(me.id, "engineer", "render the trailer")
        return [Done(final_text="ok")]

    def engineer(spec, me):
        if spec.prompt == "render the trailer":
            crew.runner.ask_permission(me.id, f"Write the trailer's frames into {renders}", "to keep them with your "
                                       "other renders", "folder", str(renders))
            return [Done(final_text="asked")]
        return [Done(final_text=f"rendered: {spec.prompt[:60]}")]

    crew.script = {"chief": chief, "engineer": engineer}
    top = crew.runner.submit("chief", "go")
    await wait_until(lambda: "thread" in state and crew.settled(state["thread"].id)())

    thread = store.get_task(state["thread"].id)
    assert thread.status is Status.NEEDS_INPUT and thread.reported  # waiting on the user, not reported as finished
    [asked] = shown(store, thread.id)
    assert asked["kind"] == "folder" and asked["folder"] == str(renders.resolve()) and asked["bot"] == "engineer"
    assert shown(store, top.id) == [asked]  # in the chat the work came from, to answer there

    crew.runner.answer_permission(asked["permission"], True)
    await wait_until(lambda: crew.settled(thread.id)() and store.get_task(thread.id).status is not Status.NEEDS_INPUT)

    assert shown(store, thread.id, "permission_answer") == shown(store, top.id, "permission_answer") == [
        {"permission": asked["permission"], "allowed": True}]
    told = crewbots_messages(store, thread.id)[-1]
    assert told.startswith("The user allowed it: Write the trailer's frames") and f"write in {renders.resolve()}" in told
    assert crew.specs_for("engineer")[-1].write_dirs == [renders.resolve()]  # from its next turn
    assert crew.runner.answer_permission(asked["permission"], False)["allowed"] == 1  # answered once


async def test_the_network_and_a_go_ahead_and_a_no(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True))
    asked = []

    def chief(spec, me):
        if spec.prompt == "go":
            asked.append(crew.runner.ask_permission(me.id, "Install the test dependencies", "pytest needs them",
                                                    "network"))
            asked.append(crew.runner.ask_permission(me.id, "Delete the 40 GB of old builds"))
        return [Done(final_text="ok")]

    crew.script["chief"] = chief
    top = crew.runner.submit("chief", "go")
    await wait_until(crew.settled(top.id))
    assert store.get_task(top.id).status is Status.NEEDS_INPUT

    crew.runner.answer_permission(asked[0]["id"], True)
    await wait_until(lambda: crew.settled(top.id)() and len(crew.specs_for("chief")) == 2)
    crew.runner.answer_permission(asked[1]["id"], False)
    await wait_until(lambda: crew.settled(top.id)() and len(crew.specs_for("chief")) == 3)

    assert crew.specs_for("chief")[1].network is True and crew.specs_for("chief")[0].network is False
    told = crewbots_messages(store, top.id)
    assert told[0].startswith("The user allowed it: Install the test dependencies. You have the network")
    assert told[1].startswith("The user didn't allow it: Delete the 40 GB of old builds.")
    assert [g["kind"] for g in store.grants(top.id)] == ["network"]  # a go-ahead or a no grants nothing


def test_what_cant_be_asked_for(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True, network=True),
                        bot("engineer", access=Access.WORKSPACE_WRITE))
    chief, engineer = store.create_task("chief", "go"), store.create_task("engineer", "go")
    home = paths.home()
    (home / ".ssh").mkdir()
    (home / "Library" / "Mail").mkdir(parents=True)
    (paths.root() / "crew").mkdir(parents=True)
    own = crew.runner.crew.folder_of(crew.runner.crew.bots["engineer"])
    own.mkdir(parents=True)

    for folder, why in [(str(home), "not the home folder itself"), ("/etc", "inside the home folder"),
                        ("Documents", "whole path"), (str(home / "nowhere"), "no folder"),
                        (str(home / ".ssh"), "logins or keys"), (str(home / "Library" / "Mail"), "Library"),
                        (str(paths.root() / "crew"), "crewbots' own")]:
        with pytest.raises(CrewError, match=why):
            crew.runner.ask_permission(chief.id, "write there", kind="folder", folder=folder)
    with pytest.raises(CrewError, match="in your task's own folder"):
        crew.runner.ask_permission(engineer.id, "write here", kind="folder", folder=str(own))
    with pytest.raises(CrewError, match="the network already"):
        crew.runner.ask_permission(chief.id, "browse", kind="network")
    with pytest.raises(CrewError, match="kind is one of"):
        crew.runner.ask_permission(chief.id, "push it", kind="push")
    assert shown(store, chief.id) == []


async def test_a_session_opens_again_with_what_was_allowed(store, tmp_path):
    runner, backend = make(store, tmp_path, SessionBackend())
    task = runner.submit("worker", "first")
    await wait_until(settled(store, runner, task.id))
    asked = runner.ask_permission(task.id, "Look things up online", kind="network")

    runner.answer_permission(asked["id"], True)
    await wait_until(lambda: len(backend.opened) == 2 and settled(store, runner, task.id)())

    assert backend.opened[0].closed and backend.opened[1].spec.network is True
    assert backend.opened[1].spec.resume_session_id == backend.opened[0].session_id  # the same conversation


def test_a_granted_folder_is_writable_and_nowhere_else(tmp_path):
    reports = tmp_path / "Reports"
    reports.mkdir()
    reader = Policy(Access.READ_ONLY, tmp_path / "own", set(), write_dirs=[reports])
    writer = Policy(Access.WORKSPACE_WRITE, tmp_path / "own", set(), write_dirs=[reports])

    assert reader.check_tool("Write", {"file_path": str(reports / "q3.md")}) is None
    assert reader.check_tool("Write", {"file_path": str(tmp_path / "other.md")}) == READ_ONLY
    assert reader.check_tool("Bash", {"command": "ls"}) == READ_ONLY  # a folder, not a shell
    assert writer.check_write(str(reports / "q3.md")) is None
    assert "ask_permission" in writer.check_write(str(tmp_path / "other.md"))

    spec = RunSpec(prompt="", cwd=tmp_path / "own", access=Access.READ_ONLY, write_dirs=[reports])
    assert f'"{reports}" = "write"' in " ".join(sandbox_overrides(spec))  # Codex
    assert settings(Access.READ_ONLY, [reports])["permissions"]["allow"] == [f"Edit(/{reports})", f"Edit(/{reports}/**)"]


def test_a_bot_asks_through_the_api_and_only_the_user_answers(crew_app):  # noqa: F811
    client, backend, store = crew_app
    task_id, token = start(client, backend, "chief")

    asked = client.post("/bot/ask_permission", json={"what": "Use the network", "kind": "network"}, headers=as_bot(token))
    permission = asked.json()["id"]

    assert asked.status_code == 201
    assert client.post(f"/permissions/{permission}", json={"allowed": True}, headers=as_bot(token)).status_code == 403
    user = {"Authorization": f"Bearer {ADMIN}"}
    answered = client.post(f"/permissions/{permission}", json={"allowed": True}, headers=user)
    assert answered.json()["allowed"] is True and [g["id"] for g in store.grants(task_id)] == [permission]
    assert client.post("/permissions/999", json={"allowed": True}, headers=user).status_code == 404
