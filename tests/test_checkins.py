"""Check-ins: a bot's checklist of things to look at now and then, checked in a small run of its own, saying something
only when there's something worth the user's attention."""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from fakes import FakeBackend, bot, make_crew, wait_until
from fastapi.testclient import TestClient

from crewbots import checkins
from crewbots.api import create_app
from crewbots.events import Done, Usage
from crewbots.runner import Runner
from crewbots.scheduler import Scheduler
from crewbots.store import Status, Store

CHECKLIST = """# Check-in

Active hours: 9-18
- every 2h: pull requests waiting on the user for over a day
- every 1d: dependencies with a security advisory
- every 5m: the build
Not an item: ignored.
"""


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def crew_with(store, tmp_path, reply, checklist=CHECKLIST, gate=None):
    (tmp_path / "bots" / "engineer").mkdir(parents=True)
    if checklist:
        (tmp_path / "bots" / "engineer" / "checkin.md").write_text(checklist)
    backend = FakeBackend(reply=reply, gate=gate)
    runner = Runner(store, make_crew(tmp_path, bot("engineer"), bot("critic")), {"fake": backend})
    return runner, Scheduler(runner), backend


def says(report):
    return lambda spec: [Usage(input_tokens=2000, output_tokens=60), Done(final_text=json.dumps(report))]


def at(hour, minute=0):
    return datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_a_checklist_has_items_with_intervals_and_active_hours(tmp_path):
    (tmp_path / "bots" / "engineer").mkdir(parents=True)
    checkins.path(tmp_path, "engineer").write_text(CHECKLIST)
    checklist = checkins.load(tmp_path, "engineer")

    assert [(i.every, i.what) for i in checklist.items] == [
        (timedelta(hours=2), "pull requests waiting on the user for over a day"),
        (timedelta(days=1), "dependencies with a security advisory"),
        (timedelta(minutes=15), "the build")]  # no more often than every 15 minutes
    assert checklist.hours == (9, 18) and checklist.active(at(10)) and not checklist.active(at(19))
    first, second, third = checklist.items
    last = {first.id: (at(10) - timedelta(hours=3)).isoformat(), second.id: (at(10) - timedelta(hours=3)).isoformat()}
    assert checklist.due(last, at(10)) == [first, third]  # the daily one isn't due; the never-run one is

    assert checkins.load(tmp_path, "critic") is None  # no checklist, no cost
    checkins.path(tmp_path, "engineer").write_text("# Check-in\n\nNothing yet.\n")
    assert checkins.load(tmp_path, "engineer") is None


async def test_a_check_with_news_says_it_in_the_bots_chat(store, tmp_path):
    runner, scheduler, backend = crew_with(store, tmp_path, says(
        {"notify": True, "urgency": "high", "text": "PR acme/web#412 has waited two days on you.", "start_thread": None}))
    runner.crew.memory.remember("engineer", "Reviews", "The user reviews in the morning.", "t_1")

    task = scheduler._check_in_due(at(10))
    await wait_until(lambda: task.id not in runner.running())

    said = [e.data for e in store.events(runner.chat("engineer").id) if e.kind == "text"]
    assert said == [{"text": "PR acme/web#412 has waited two days on you.", "checkin": True, "urgency": "high"}]
    done = store.get_task(task.id)
    assert done.status is Status.IDLE and done.created_by == "crewbots" and done.title == "engineer's check-ins"
    spec = backend.specs[0]  # small, read-only, the bot's own model, no crew tools
    assert spec.access.value == "read_only" and "crewbots" not in spec.mcp_servers and "in the background" in spec.system_prompt
    assert "pull requests waiting on the user" in spec.prompt and "The user reviews in the morning." in spec.prompt
    assert len(spec.prompt) < 6000 and store.bot_usage("engineer", "2000-01-01") == 2060

    assert scheduler._check_in_due(at(10)) is None  # nothing due again yet


async def test_nothing_to_report_is_silent_and_shares_one_task(store, tmp_path):
    runner, scheduler, _ = crew_with(store, tmp_path, says({"notify": False, "text": "", "start_thread": None}))
    first = scheduler.check_in("engineer")
    await wait_until(lambda: first.id not in runner.running())
    again = scheduler.check_in("engineer")
    await wait_until(lambda: again.id not in runner.running())

    assert first.id == again.id and store.get_task(first.id).result == "Nothing to report."
    assert [e for e in store.events(runner.chat("engineer").id) if e.kind == "text"] == []
    assert [e.kind for e in store.events(first.id) if e.kind == "checkin"] == ["checkin", "checkin"]


async def test_a_check_waits_outside_active_hours_while_the_bot_works_or_without_capacity(store, tmp_path):
    gate = asyncio.Event()
    runner, scheduler, _ = crew_with(store, tmp_path, says({"notify": False}), gate=gate)
    assert scheduler._check_in_due(at(20)) is None  # outside 9-18

    busy = runner.submit("engineer", "real work")
    await wait_until(lambda: busy.id in runner.running())
    assert scheduler._check_in_due(at(10)) is None  # the bot is busy, and so is the user
    gate.set()
    await wait_until(lambda: not runner.running())

    runner.pause()
    assert scheduler._check_in_due(at(10)) is None  # no spare capacity while paused


def test_the_app_or_cli_checks_a_bot_in_now(store, tmp_path):
    runner, _, _ = crew_with(store, tmp_path, says({"notify": False}))
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        auth = {"Authorization": "Bearer t"}
        assert client.post("/checkins/engineer", headers=auth).status_code == 201
        assert client.post("/checkins/critic", headers=auth).status_code == 422  # it has no checklist
        assert client.post("/checkins/ghost", headers=auth).status_code == 404
