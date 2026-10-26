"""Routines are proposed, not inferred: a bot proposes one, it stays off until the user turns it on and watches its
first run; and after a week with nothing from the user, routines and check-ins wait for them."""

from datetime import datetime, timedelta

import pytest
from fakes import ScriptedCrew, bot, wait_until
from fastapi.testclient import TestClient

from crewbots import routines
from crewbots.api import create_app
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done
from crewbots.scheduler import Scheduler
from crewbots.store import Store

TZ = datetime.now().astimezone().tzinfo


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def crew(store, tmp_path):
    scripted = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    scripted.script["engineer"] = lambda spec, me: [Done(final_text="checked")]
    scripted.script["chief"] = lambda spec, me: [Done(final_text="ok")]
    (tmp_path / "routines").mkdir()
    return scripted


async def test_a_bot_proposes_a_routine_that_stays_off_until_the_user_turns_it_on(crew, store, tmp_path):
    tools = CrewTools(crew.runner, crew.runner.chat("engineer").id, can_delegate=False)
    said = await tools.call("propose_routine", {"name": "dependency-check", "description": "Outdated dependencies.",
                                                "goal": "Look for outdated dependencies.", "schedule": "0 10 * * 1"})
    assert "off until the user turns it on" in said
    with pytest.raises(ToolRefused, match="already a routine"):
        await tools.call("propose_routine", {"name": "dependency-check", "description": "x", "goal": "x",
                                             "schedule": "0 10 * * 1"})
    with pytest.raises(ToolRefused):
        await tools.call("propose_routine", {"name": "bad", "description": "x", "goal": "x", "schedule": "whenever"})

    [routine], _ = routines.load(tmp_path / "routines")
    assert routine.proposed_by == "engineer" and routine.bot == "engineer" and not routine.enabled
    scheduler = Scheduler(crew.runner)
    monday_10 = datetime(2026, 11, 2, 10, 0, tzinfo=TZ)
    assert await scheduler.tick(monday_10 - timedelta(minutes=1)) == [] and await scheduler.tick(monday_10) == []

    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        auth = {"Authorization": "Bearer t"}
        listed = client.get("/routines", headers=auth).json()["routines"]
        assert [(r["name"], r["proposed_by"], r["enabled"]) for r in listed] == [("dependency-check", "engineer", False)]
        test = client.post("/routines/dependency-check/confirm", headers=auth).json()
        assert test["bot"] == "engineer"  # its first run, now, to watch
        [routine] = client.get("/routines", headers=auth).json()["routines"]
        assert routine["enabled"] and routine["proposed_by"] is None
        assert client.post("/routines/ghost/confirm", headers=auth).status_code == 404
    await wait_until(lambda: not crew.runner.running())


async def test_after_a_week_away_routines_wait_and_the_chief_says_so_once(crew, store, tmp_path):
    (tmp_path / "routines" / "standup.md").write_text("---\nbot: chief\nschedule: '0 9 * * *'\n---\nStandup.")
    chat = crew.runner.chat("chief")
    store.append_event(chat.id, kind="message", data={"from": "user", "text": "see you in a while"})
    store.db.execute("update events set at = ? where kind = 'message'", ("2026-10-01T09:00:00+00:00",))
    scheduler = Scheduler(crew.runner)
    day = datetime(2026, 10, 20, 8, 59, tzinfo=TZ)

    await scheduler.tick(day)
    assert await scheduler.tick(day + timedelta(minutes=1)) == []  # due, but the user's been away
    await scheduler.tick(day + timedelta(minutes=2))
    away = [e.data for e in store.events(chat.id) if e.kind == "text" and e.data.get("away")]
    assert len(away) == 1 and "You haven't written since" in away[0]["text"]

    crew.runner.say("chief", "I'm back")  # writing again: they carry on
    await wait_until(lambda: not crew.runner.running())
    tomorrow = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(days=1)
    await scheduler.tick(tomorrow - timedelta(minutes=1))
    assert len(await scheduler.tick(tomorrow)) == 1
    assert not (store.routine_state("away") or {}).get("last_run_at")
