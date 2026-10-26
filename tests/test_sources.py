"""Event sources: commits, branches, watched folders, GitHub and finished threads, detected with no model call; and the
standing intents and checklist items that wait for them."""

import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until
from fastapi.testclient import TestClient

from crewbots import checkins
from crewbots.api import create_app
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done
from crewbots.scheduler import Scheduler
from crewbots.sources import Sources
from crewbots.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@dataclass(frozen=True)
class PR:
    repo: str
    number: int
    title: str
    author: str = "lee"
    checks: str | None = None

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"


@dataclass(frozen=True)
class Snapshot:
    review_requested: list
    mine: list


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_commits_and_branch_switches_are_seen_after_the_first_look(tmp_path):
    repo = git_repo(tmp_path / "acme-web")
    sources = Sources()
    assert sources.git([repo]) == []  # what was already there isn't news

    (repo / "export.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Add the export")
    [event] = sources.git([repo])
    assert event.kind == "commit" and "acme-web on main:" in event.text and "Add the export" in event.text

    git(repo, "checkout", "-qb", "csv")
    assert [(e.kind, e.text) for e in sources.git([repo])] == [("branch", "acme-web: switched from main to csv")]
    assert sources.git([repo]) == []


def test_files_added_changed_and_removed_in_a_watched_folder(tmp_path):
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "old.md").write_text("a")
    (folder / "keep.md").write_text("b")
    sources = Sources()
    assert sources.folders([folder]) == []

    (folder / "new.md").write_text("c")
    (folder / "old.md").unlink()
    (folder / "keep.md").write_text("bb")
    os.utime(folder / "keep.md", (1, 1))
    [event] = sources.folders([folder])
    assert event.kind == "file" and event.text == "specs: added new.md; changed keep.md; removed old.md"


def test_review_requests_and_checks_come_from_the_github_poll():
    sources = Sources()
    assert sources.github(Snapshot([PR("acme/web", 1, "Old")], [PR("acme/web", 7, "Mine", checks="PENDING")])) == []
    events = sources.github(Snapshot([PR("acme/web", 1, "Old"), PR("acme/web", 2, "New toolbar")],
                                     [PR("acme/web", 7, "Mine", checks="FAILURE")]))
    assert [(e.kind, e.text) for e in events] == [
        ("review_requested", "lee asked you to review acme/web#2: New toolbar"),
        ("ci_failed", "checks failed on acme/web#7: Mine")]
    [passed] = sources.github(Snapshot([], [PR("acme/web", 7, "Mine", checks="SUCCESS")]))
    assert (passed.kind, passed.subject) == ("ci_passed", "acme/web#7")


async def test_a_standing_intent_tells_its_bot_when_its_event_happens(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    crew.script["engineer"] = lambda spec, me: [Done(final_text="noted")]
    tools = CrewTools(crew.runner, crew.runner.chat("engineer").id, can_delegate=False)
    with pytest.raises(ToolRefused, match="no event 'deploy'"):
        await tools.call("remind_on", {"event": "deploy", "then": "x"})
    said = await tools.call("remind_on", {"event": "ci_passed", "match": "acme/web#7",
                                          "then": "Tell the user to cut the release."})
    assert said == "Standing intent 1 set: on ci_passed acme/web#7."

    crew.runner.happened("ci_passed", "acme/web#8", "checks passed on acme/web#8")  # not its pull request
    crew.runner.happened("ci_failed", "acme/web#7", "checks failed on acme/web#7")  # not its event
    crew.runner.happened("ci_passed", "acme/web#7", "checks passed on acme/web#7: Mine")
    await wait_until(lambda: not crew.runner.running())
    told = [e.data for e in store.events(crew.runner.chat("engineer").id) if e.kind == "message"]
    assert told[-1]["from"] == "crewbots" and "Tell the user to cut the release." in told[-1]["text"]
    assert "checks passed on acme/web#7" in told[-1]["text"] and store.intents()[0]["fires"] == 1

    crew.runner.happened("ci_passed", "acme/web#7", "checks passed again")  # within a day: not again
    assert store.intents()[0]["fires"] == 1 and len(store.crew_events()) == 4
    store.db.execute("update intents set fires = 3")
    assert store.intents() == []  # fired out


async def test_a_thread_finishing_is_an_event(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    crew.script["engineer"] = lambda spec, me: [Done(final_text="done")]
    thread = crew.runner.submit("engineer", "Add the export")
    crew.runner.say("engineer", "hi")  # a chat's turn isn't one
    await wait_until(lambda: not crew.runner.running())
    assert [(e["kind"], e["subject"]) for e in store.crew_events()] == [("thread_done", thread.id)]


def test_checklist_items_can_wait_for_events_and_watch_folders(tmp_path):
    (tmp_path / "bots" / "engineer").mkdir(parents=True)
    checkins.path(tmp_path, "engineer").write_text(
        "Watch: ~/specs\n- on commit acme-web: what changed\n- on deploy: not a kind, so not an item\n- every 1d: x\n")
    checklist = checkins.load(tmp_path, "engineer")
    on, daily = checklist.items
    assert (on.on, on.match, on.what, on.every) == ("commit", "acme-web", "what changed", None)
    assert checklist.watch[0].name == "specs" and not str(checklist.watch[0]).startswith("~")

    now = datetime.now(UTC)
    old = (now - timedelta(hours=2)).isoformat()
    events = [{"kind": "commit", "subject": "/r/acme-api", "text": "acme-api on main: x", "at": now.isoformat()}]
    assert checklist.due({daily.id: now.isoformat()}, now, events, since=old) == []  # another repository
    events.append({"kind": "commit", "subject": "/r/acme-web", "text": "acme-web on main: y", "at": now.isoformat()})
    assert checklist.due({daily.id: now.isoformat()}, now, events, since=old) == [on]
    assert checklist.due({daily.id: now.isoformat(), on.id: now.isoformat()}, now, events) == []  # seen already


async def test_the_scheduler_looks_at_the_bots_workspaces(store, tmp_path):
    repo = git_repo(tmp_path / "acme-web")
    crew = ScriptedCrew(store, tmp_path, bot("engineer", workspace=repo))
    scheduler = Scheduler(crew.runner, sources_every_s=0)
    await scheduler.tick()
    (repo / "a.py").write_text("a")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Add a")
    await scheduler.tick()
    assert [e["kind"] for e in store.crew_events()] == ["commit"]

    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        auth = {"Authorization": "Bearer t"}
        assert client.get("/crew-events", headers=auth).json()[0]["text"].endswith("Add a")
        intent = crew.runner.remind_on(crew.runner.chat("engineer").id, "commit", "", "Look at it.")
        assert [i["id"] for i in client.get("/intents", headers=auth).json()] == [intent["id"]]
        assert client.delete(f"/intents/{intent['id']}", headers=auth).json()["cancelled"] is True
        assert client.get("/intents", headers=auth).json() == []
