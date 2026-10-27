"""Starting a crew off: the chief's first word in its chat, kept like its other messages, and bringing in what Claude
Code and Codex know, learned in a thread of the chief's own."""

import dataclasses

import pytest
from fakes import FakeBackend, bot, make_crew, wait_until

from crewbots import discovery, paths
from crewbots.runner import Runner
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
    assert any(e.kind == "message" and "Learn what Claude Code and Codex know" in e.data["text"]
               for e in store.events(chat.id))  # the thread's result came back to the chat
    assert runner.started() and runner.greet() is None


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
