"""Starting a crew off: the chief's first word in its chat, kept like its other messages, and bringing in what Claude
Code and Codex know, learned in a thread of the chief's own."""

import dataclasses

import pytest
from fakes import FakeBackend, bot, make_crew, wait_until

from crewbots import cards, discovery, paths
from crewbots import crew as crew_module
from crewbots.crew import CrewError
from crewbots.runner import NotAllowed, Runner
from crewbots.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def runner_for(store, tmp_path, **chief):
    return Runner(store, make_crew(tmp_path / "crew", bot("chief", can_delegate=True, **chief), bot("engineer")),
                  {"fake": FakeBackend()})


def kinds(store, task_id):
    return [(e.kind, e.data.get("text") or e.data) for e in store.events(task_id)]


def test_the_chief_says_hello_once_with_an_offer_under_it(store, tmp_path):
    runner = runner_for(store, tmp_path, display_name="Roger")

    chat = runner.greet()
    assert runner.greet() is None  # once

    said = kinds(store, chat.id)
    assert said[0] == ("text", "Hey! I'm Roger, your Chief.")
    assert said[1][1].startswith("Message me anytime. I'll keep things moving")
    assert said[-1] == ("onboarding", {"offer": "bring_in"})
    assert all(e.data.get("greeting") for e in store.events(chat.id) if e.kind == "text")


def test_a_chief_with_no_name_asks_for_one(store, tmp_path):
    runner = runner_for(store, tmp_path)
    chat = runner.greet()
    assert ("text", "Want to give me a name?") in kinds(store, chat.id)


def test_the_chief_carries_on_from_its_hello(store, tmp_path):
    """Its first session starts from what it said, which crewbots said for it."""
    runner = runner_for(store, tmp_path)
    briefing = store.get_task(runner.greet().id).briefing
    assert "Hey! I'm your Chief." in briefing and "Want to give me a name?" in briefing
    assert "rather than introducing yourself again" in briefing and "`set_name`" in briefing


def test_code_review_alone_isnt_meeting_the_crew(store, tmp_path):
    review = store.create_task("reviewer:claude:sonnet:high", "Review acme/web#1", kind="review")
    store.append_event(review.id, kind="message", data={"from": "user", "text": "Review acme/web#1"})

    assert runner_for(store, tmp_path).greet() is not None


def test_a_crew_the_user_has_talked_with_isnt_greeted(store, tmp_path):
    runner = runner_for(store, tmp_path)
    chat = runner.chat("chief")
    store.append_event(chat.id, kind="message", data={"from": "user", "text": "Morning"})

    assert runner.greet() is None
    assert runner.started() and kinds(store, chat.id) == [("message", "Morning")]


async def test_bringing_in_adds_projects_and_learns_in_a_thread_of_its_own(store, tmp_path):
    runner = runner_for(store, tmp_path, display_name="Roger")
    chat = runner.greet()
    game = paths.home() / "Games" / "Sky Islands"
    game.mkdir(parents=True)
    memory = paths.home() / "art.md"
    memory.write_text("Stylized art, never painted pavements.")

    added, thread = runner.bring_in([{"name": "sky-islands", "folder": str(game), "about": "A cozy game."}],
                                    [discovery.FoundMemory(memory, "claude", "Art", game)])
    await wait_until(lambda: not runner.running())

    assert [p.name for p in added] == ["sky-islands"] and "sky-islands" in runner.crew.projects
    assert thread.parent_id == chat.id and thread.title == "Learn what Claude Code and Codex know"
    first = store.events(thread.id)[0]
    assert first.data["from"] == "crewbots" and "never painted pavements" in first.data["text"]  # out of view
    assert ("onboarding", {"brought": {"projects": ["sky-islands"], "memories": 1, "thread": thread.id}}) in kinds(
        store, chat.id)
    told = [e.data["text"] for e in store.events(chat.id) if e.kind == "message" and e.data["from"] == "crewbots"]
    assert told[-1].startswith("The thread you opened to learn what Claude Code and Codex know has finished")
    assert store.get_task(thread.id).result in told[-1] and "The question, with `escalate`: where to start" in told[-1]
    assert runner.started() and runner.greet() is None


async def test_folders_alone_are_still_learned_about(store, tmp_path):
    runner = runner_for(store, tmp_path)
    runner.greet()
    game = paths.home() / "Games" / "Gold Hauler"
    game.mkdir(parents=True)

    _, thread = runner.bring_in([{"name": "gold-hauler", "folder": str(game)}], [])
    await wait_until(lambda: not runner.running())

    assert thread is not None and "(none)" in store.events(thread.id)[0].data["text"]  # it looks at the folders


def test_not_now_is_remembered(store, tmp_path):
    runner = runner_for(store, tmp_path)
    chat = runner.greet()

    runner.not_now()

    assert kinds(store, chat.id)[-1] == ("onboarding", {"declined": True})
    assert runner.started()


def test_a_crew_already_started_off_isnt_greeted(store, tmp_path):
    runner = runner_for(store, tmp_path)
    project = paths.home() / "web"
    project.mkdir()
    runner.reload(dataclasses.replace(runner.crew, projects={"web": object()}))

    assert runner.greet() is None


def test_a_bot_takes_the_name_the_user_gives_it_in_its_chat(store, tmp_path):
    home = crew_module.init(tmp_path / "crew")
    runner = Runner(store, crew_module.load(home), {"fake": FakeBackend()})
    chat = runner.chat("chief")

    with runner.bus.subscribe() as heard:
        runner.set_name(chat.id, "  Roger ")
        told = [heard.get_nowait()["type"] for _ in range(heard.qsize())]

    assert "bots" in told  # the app reads the bots again, so the new name shows
    assert runner.crew.bots["chief"].label == "Roger" and cards.card(home, "chief")["display_name"] == "Roger"
    assert crew_module.load(home).bots["chief"].mcp == runner.crew.bots["chief"].mcp  # the rest of its card as it was
    with pytest.raises(NotAllowed):  # work isn't where it's named
        runner.set_name(store.create_task("chief", "Plan the launch").id, "Rog")
    for taken in ("Critic", "R@ger", "x" * 41):
        with pytest.raises(CrewError):
            runner.set_name(chat.id, taken)
