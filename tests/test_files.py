"""Files a bot shows the user: ones it shares, copied into the crew's attachments and shown under what it says, from
its own folders only."""

import pytest
from fakes import ScriptedCrew, bot, wait_until
from test_bot_api import ADMIN, as_bot, crew_app, start  # noqa: F401

from crewbots.backends import Access
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done
from crewbots.store import Store

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def crew(store, tmp_path):
    (tmp_path / "studio").mkdir()
    scripted = ScriptedCrew(store, tmp_path, bot("artist", Access.WORKSPACE_WRITE, workspace=tmp_path / "studio"),
                            bot("critic"))
    scripted.script["artist"] = scripted.script["critic"] = lambda spec, me: [Done(final_text="ok")]
    return scripted


async def test_a_shared_file_is_a_copy_shown_under_the_bots_message(crew, store, tmp_path):
    (tmp_path / "studio" / "renders").mkdir()
    (tmp_path / "studio" / "renders" / "shot.png").write_bytes(PNG)
    chat = crew.runner.chat("artist")
    said = await CrewTools(crew.runner, chat.id, can_delegate=False).call(
        "share", {"files": ["renders/shot.png"], "text": "The first pass."})
    assert said.startswith("Shared shot.png with the user")

    [shared] = [e for e in store.events(chat.id) if e.kind == "shared"]
    [copy] = shared.data["attachments"]
    assert shared.data["text"] == "The first pass." and copy["kind"] == "image" and copy["mime"] == "image/png"
    assert copy["path"].startswith(str(tmp_path / "attachments"))
    (tmp_path / "studio" / "renders" / "shot.png").write_bytes(b"changed")  # the bot carries on; what it showed stays
    assert open(copy["path"], "rb").read() == PNG
    assert [h["task_id"] for h in store.search("pass")] == [chat.id]  # found by what was said with it


async def test_only_files_in_the_bots_own_folders(crew, store, tmp_path):
    (tmp_path / "secrets.txt").write_text("not for sharing")
    (tmp_path / "studio" / "link.txt").symlink_to(tmp_path / "secrets.txt")
    chat = crew.runner.chat("artist")
    tools = CrewTools(crew.runner, chat.id, can_delegate=False)
    for path in (str(tmp_path / "secrets.txt"), "../secrets.txt", "link.txt", "~/.ssh/id_ed25519"):
        with pytest.raises(ToolRefused, match="isn't in your folders"):
            await tools.call("share", {"files": [path]})
    assert store.agent_log()[0]["kind"] == "refused"
    with pytest.raises(ToolRefused, match="isn't a file"):
        await tools.call("share", {"files": ["missing.png"]})
    with pytest.raises(ToolRefused, match="at most 10"):
        await tools.call("share", {"files": [f"{n}.png" for n in range(11)]})
    assert not (tmp_path / "attachments").exists()  # nothing copied for a share that was refused

    own = tmp_path / "work" / "critic"  # a bot with no folder of its own shares from its folder in the crew
    own.mkdir(parents=True)
    (own / "notes.md").write_text("# Notes")
    with pytest.raises(ToolRefused, match="isn't in your folders"):  # a reader works in the crew's folder
        await CrewTools(crew.runner, crew.runner.chat("critic").id, False).call("share", {"files": ["notes.md"]})
    said = await CrewTools(crew.runner, crew.runner.chat("critic").id, False).call(
        "share", {"files": [str(own / "notes.md")]})
    assert "notes.md" in said


async def test_in_a_group_chat_shared_files_are_posted_there(crew, store, tmp_path):
    (tmp_path / "studio" / "cut.mp4").write_bytes(b"\0" * 2048)
    room = crew.runner.create_room("Launch video", ["artist", "critic"], "artist")
    crew.runner.post(room["id"], "user", "show us the cut")
    await wait_until(lambda: store.seat_of(room["id"], "artist") is not None)
    seat = store.seat_of(room["id"], "artist")
    await CrewTools(crew.runner, seat.id, can_delegate=False).call("share", {"files": ["cut.mp4"], "text": "@critic thoughts?"})
    posted = [m for m in store.room_messages(room["id"]) if m["attachments"]]
    assert [(m["author"], m["text"], m["attachments"][0]["mime"]) for m in posted] == [
        ("artist", "@critic thoughts?", "video/mp4")]
    await wait_until(lambda: store.seat_of(room["id"], "critic") is not None)  # it woke the teammate it mentioned
    await wait_until(lambda: not crew.runner.running())

    said = await CrewTools(crew.runner, crew.runner.chat("artist").id, False).call(
        "share", {"files": ["cut.mp4"], "room": room["id"]})
    assert f"in the group chat ({room['id']})" in said


def test_codex_bots_share_through_the_bot_api(crew_app, tmp_path):  # noqa: F811
    client, backend, store = crew_app
    (tmp_path / "chart.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    task_id, token = start(client, backend, "engineer")
    store.update_task(task_id, workspace=str(tmp_path))
    shared = client.post("/bot/share", json={"files": ["chart.svg"]}, headers=as_bot(token))
    assert shared.status_code == 201 and shared.json()["files"][0]["mime"] == "image/svg+xml"
    assert client.post("/bot/share", json={"files": ["nope.svg"]}, headers=as_bot(token)).status_code == 422
    assert client.post("/bot/share", json={"files": ["/etc/hosts"]}, headers=as_bot(token)).status_code == 403
