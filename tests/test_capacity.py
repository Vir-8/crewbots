"""Spare capacity: background work runs only with headroom, never beside the user's own work, and says why."""

import asyncio
import time
from datetime import datetime

import pytest
from fakes import FakeBackend, bot, make_crew, wait_until

from crewbots.events import RateLimit
from crewbots.runner import Runner
from crewbots.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def runner_for(store, tmp_path, gate=None):
    return Runner(store, make_crew(tmp_path, bot("engineer")), {"fake": FakeBackend(gate=gate)})


def window(name, used, resets_in_s=None):
    return {"name": name, "used_percent": used, "resets_at": time.time() + resets_in_s if resets_in_s else None}


async def test_it_runs_with_headroom_and_says_how_much(store, tmp_path):
    runner = runner_for(store, tmp_path)
    plans = {"claude": {"windows": [window("5 hours", 36), window("Week", 20)]}}
    runner.capacity.source = lambda: asyncio.sleep(0, plans)
    await runner.capacity.refresh()

    verdict = runner.capacity.verdict("claude")
    assert verdict.go and verdict.reason == "running, 64% left in the 5 hours window" and verdict.left == 64

    plans["claude"]["windows"] = [window("5 hours", 85), window("Week", 20)]
    assert runner.capacity.verdict("claude").reason == "waiting: 15% left in the 5 hours window, under 20%"

    plans["claude"]["windows"] = [window("5 hours", 30, resets_in_s=1800), window("Week", 50)]
    verdict = runner.capacity.verdict("claude")
    assert verdict.go and "the 5 hours window resets soon with 70% unused" in verdict.reason


async def test_it_waits_when_the_cli_says_a_limit_is_close_or_the_crew_is_paused(store, tmp_path):
    runner = runner_for(store, tmp_path)
    runner.limits["claude"] = {"seven_day": RateLimit(plan="claude", window="seven_day", status="warning")}
    assert runner.capacity.verdict("claude").reason == "waiting: the Claude plan's seven day limit is close"
    assert runner.capacity.verdict("codex").go  # another plan's limits are its own
    runner.limits.clear()
    runner.pause()
    assert runner.capacity.verdict("claude").reason == "waiting while the crew is paused"


async def test_it_waits_while_the_users_own_work_runs_but_not_for_its_own(store, tmp_path):
    gate = asyncio.Event()
    runner = runner_for(store, tmp_path, gate)
    mine = runner.submit("engineer", "the user's work")
    await wait_until(lambda: mine.id in runner.running())
    assert runner.user_busy() and runner.capacity.verdict("claude").reason == "waiting while your own work runs"
    assert not runner.dream_due("engineer", datetime.now().astimezone().replace(hour=4))  # consolidation waits too
    gate.set()
    await wait_until(lambda: not runner.running())

    gate.clear()
    background = store.create_task("engineer", "Consolidate engineer's memory", created_by="crewbots")
    runner.send(background.id, "go")
    await wait_until(lambda: background.id in runner.running())
    assert not runner.user_busy() and runner.capacity.verdict("claude").go
    gate.set()
    await wait_until(lambda: not runner.running())


async def test_status_says_whether_background_work_runs_and_why(store, tmp_path):
    runner = runner_for(store, tmp_path)
    task = runner.submit("engineer", "x")
    await wait_until(lambda: not runner.running())
    status = await runner.status(task.id)
    assert status["background"] == "running (its plan's limits aren't known yet)"
