"""Budgets and plan limits: a turn that would go past one waits, instead of failing mid-task."""

import asyncio
import time
from dataclasses import replace

import pytest
from fakes import FakeBackend, bot, make_crew, reply, wait_until
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.backends.codex_app_server import AppServerParser
from crewbots.crew import Budget, CrewError, parse_card
from crewbots.events import RateLimit
from crewbots.runner import Runner
from crewbots.store import Status, Store

# Each fake turn uses 12 fresh tokens: 10 in, 2 out, none cached.


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def runner_with(store, tmp_path, *bots, reply=reply, **kw):
    return Runner(store, make_crew(tmp_path, *bots), {"fake": FakeBackend(reply=reply)}, **kw)


def status_is(store, task_id, status):
    return lambda: store.get_task(task_id).status == status


async def test_a_bot_past_its_budget_is_held_and_goes_on_when_released(store, tmp_path):
    runner = runner_with(store, tmp_path, bot(budget=Budget(tokens=20, per="day")))
    task = runner.submit("worker", "one")
    await wait_until(status_is(store, task.id, Status.IDLE))
    runner.send(task.id, "two")
    await wait_until(lambda: store.get_task(task.id).turns == 2)
    runner.send(task.id, "three")

    await wait_until(status_is(store, task.id, Status.HELD))
    [held] = [e for e in store.events(task.id) if e.kind == "held"]
    assert "worker has used 24 of its 20 tokens for the last day" in held.data["reason"]
    assert runner.state()["held"] == 1 and runner.state()["running"] == 0
    assert runner.budgets()[0] | {"held": None} == {"bot": "worker", "tokens": 20, "per": "day", "used": 24, "held": None}

    runner.release(task.id)
    await wait_until(lambda: store.get_task(task.id).turns == 3)
    assert store.get_task(task.id).result == "did: three" and runner.state()["held"] == 0


async def test_a_task_waiting_on_its_budget_doesnt_keep_the_mac_awake(store, tmp_path):
    runner = runner_with(store, tmp_path, bot(budget=Budget(tokens=5, per="day")), keep_awake=("sleep", "30"))
    task = runner.submit("worker", "one")
    await wait_until(status_is(store, task.id, Status.IDLE))
    runner.send(task.id, "two")
    await wait_until(status_is(store, task.id, Status.HELD))

    assert runner._awake is None
    await runner.cancel(task.id)
    assert store.get_task(task.id).status is Status.CANCELLED


async def test_a_raised_budget_counts_once_the_crew_is_reloaded(store, tmp_path):
    runner = runner_with(store, tmp_path, bot(budget=Budget(tokens=5, per="week")))
    task = runner.submit("worker", "one")
    await wait_until(status_is(store, task.id, Status.IDLE))
    runner.send(task.id, "two")
    await wait_until(status_is(store, task.id, Status.HELD))

    runner.reload(make_crew(tmp_path, bot(budget=Budget(tokens=1000, per="week"))))

    await wait_until(lambda: store.get_task(task.id).turns == 2)


async def test_bots_without_a_budget_are_never_held(store, tmp_path):
    runner = runner_with(store, tmp_path, bot())
    for n in range(3):
        task = runner.submit("worker", f"task {n}")
        await wait_until(status_is(store, task.id, Status.IDLE))

    assert runner.budgets()[0]["used"] == 36 and runner.budgets()[0]["tokens"] is None


def limited(spec):
    return [RateLimit(plan="claude", window="five_hour", status="limited", used_percent=100.0,
                      resets_at=int(time.time()) + 3600), *reply(spec)]


async def test_the_plans_limit_holds_every_bot_on_that_plan_until_it_resets(store, tmp_path):
    on_claude = replace(bot(), backend="claude")
    runner = Runner(store, make_crew(tmp_path, on_claude), {"claude": FakeBackend(reply=limited)})
    first = runner.submit("worker", "one")
    await wait_until(status_is(store, first.id, Status.IDLE))

    [notice] = [e for e in store.events(first.id) if e.kind == "rate_limit"]
    assert notice.data["status"] == "limited" and runner.state()["limits"][0]["window"] == "five_hour"
    second = runner.submit("worker", "two")
    await wait_until(status_is(store, second.id, Status.HELD))
    assert "the Claude plan's five-hour limit is reached; it resets" in store.events(second.id)[-1].data["reason"]

    runner.limits["claude"]["five_hour"] = RateLimit(plan="claude", window="five_hour", status="ok")
    runner.reload(runner.crew)  # wakes held tasks to look again, as the minute-by-minute check would
    await wait_until(status_is(store, second.id, Status.IDLE))


@pytest.mark.parametrize("wake", ["reload", "release"])
async def test_waking_tasks_still_over_the_limit_doesnt_block_other_work(store, tmp_path, monkeypatch, wake):
    bots = [replace(bot(name), backend="claude") for name in ("one", "two")]
    gate = asyncio.Event()
    runner = Runner(store, make_crew(tmp_path, *bots), {"claude": FakeBackend(gate=gate)})
    runner.limits["claude"] = {"five_hour": RateLimit(
        plan="claude", window="five_hour", status="limited", resets_at=int(time.time()) + 3600)}
    tasks = [runner.submit(b.name, "wait for capacity") for b in bots]
    await wait_until(lambda: all(store.get_task(t.id).status is Status.HELD for t in tasks))
    checks = 0
    reason = runner.hold_reason

    def bounded_reason(task):
        nonlocal checks
        checks += 1
        # Fail a busy loop without hanging pytest's event loop and its timeouts too.
        if checks > 100:
            raise RuntimeError("held tasks are spinning without yielding")
        return reason(task)

    monkeypatch.setattr(runner, "hold_reason", bounded_reason)
    try:
        if wake == "release":
            runner.release(tasks[0].id)
            await wait_until(status_is(store, tasks[0].id, Status.RUNNING))
        else:
            runner.reload(runner.crew)
        await asyncio.sleep(0.02)
        assert checks < 10
        assert store.get_task(tasks[1].id).status is Status.HELD

        # A second wake must reach all remaining waiters after another turn has completed.
        gate.set()
        if wake == "release":
            await wait_until(status_is(store, tasks[0].id, Status.IDLE))
        runner.limits["claude"].clear()
        runner.reload(runner.crew)
        await wait_until(lambda: all(store.get_task(t.id).status is Status.IDLE for t in tasks))
    finally:
        await runner.shutdown()


async def test_a_limit_is_recorded_when_it_changes_not_on_every_turn(store, tmp_path):
    def warning(spec):
        return [RateLimit(plan="codex", window="5h", status="warning", used_percent=85.0), *reply(spec)]

    runner = runner_with(store, tmp_path, bot(), reply=warning)
    task = runner.submit("worker", "one")
    await wait_until(status_is(store, task.id, Status.IDLE))
    runner.send(task.id, "two")
    await wait_until(lambda: store.get_task(task.id).turns == 2)

    assert [e.data["status"] for e in store.events(task.id) if e.kind == "rate_limit"] == ["warning"]


def test_codex_reports_how_much_of_each_window_is_used():
    snapshot = {"primary": {"usedPercent": 91.5, "windowDurationMins": 300, "resetsAt": 1800000000},
                "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 1800500000}}

    events = AppServerParser().feed("account/rateLimits/updated", {"rateLimits": snapshot})

    assert events == [RateLimit(plan="codex", window="5h", status="warning", used_percent=91.5, resets_at=1800000000),
                      RateLimit(plan="codex", window="7d", status="ok", used_percent=40.0, resets_at=1800500000)]


def test_a_budget_on_a_role_card():
    card = "---\ndescription: d\nbudget:\n  tokens: 2000000\n  per: week\n---\nbody"

    assert parse_card(card, "x").budget == Budget(tokens=2_000_000, per="week")
    with pytest.raises(CrewError, match="budget is"):
        parse_card("---\ndescription: d\nbudget: 5\n---\nbody", "x")


def test_budgets_routines_and_release_over_the_api(store, tmp_path):
    runner = runner_with(store, tmp_path, bot("chief", budget=Budget(tokens=1000, per="day")))
    (tmp_path / "routines").mkdir()
    (tmp_path / "routines" / "standup.md").write_text("---\nbot: chief\nschedule: '0 9 * * 1-5'\n---\nStandup.")
    (tmp_path / "routines" / "bad.md").write_text("---\nbot: chief\n---\nx")
    (tmp_path / "bots").mkdir()
    (tmp_path / "bots" / "chief.md").write_text("---\ndescription: d\nbackend: claude\n---\nYou are the chief.")
    auth = {"Authorization": "Bearer t"}
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        listing = client.get("/routines", headers=auth).json()
        assert [r["name"] for r in listing["routines"]] == ["standup"] and "bad.md" in listing["errors"]
        assert listing["routines"][0]["next_run_at"] is not None

        task = client.post("/routines/standup/run", headers=auth).json()
        assert task["kind"] == "chat" and task["bot"] == "chief"
        assert client.post("/routines/nope/run", headers=auth).status_code == 422
        for _ in range(100):
            if client.get(f"/tasks/{task['id']}", headers=auth).json()["status"] == "idle":
                break
            time.sleep(0.02)

        assert client.get("/budgets", headers=auth).json()[0]["used"] == 12
        assert client.post(f"/tasks/{task['id']}/release", headers=auth).json()["id"] == task["id"]
        assert client.post("/crew/reload", headers=auth).status_code == 200
        starters = [s["name"] for s in client.get("/routines", headers=auth).json()["starters"]]
        assert "standup" not in starters and "retro" in starters  # standup.md is already there
        assert client.post("/routines/starters/retro", headers=auth).json()["schedule"] == "0 16 * * 5"
        assert client.get("/bots", headers=auth).json()[0]["path"].endswith("bots/chief.md")
        assert runner.crew.bots["chief"].backend == "claude"  # read from the folder again
