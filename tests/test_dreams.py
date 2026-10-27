"""Consolidating memory overnight: the bot decides, crewbots applies it to the notes and CORE.md, and commits the
memory folder; the scheduler runs it once a night, one bot at a time, when the crew is idle with room in its plan."""

import json
import subprocess
import time
from datetime import datetime, timedelta

import pytest
from fakes import FakeBackend, bot, make_crew, wait_until
from fastapi.testclient import TestClient

from crewbots import paths
from crewbots.api import create_app
from crewbots.events import Done, RateLimit, Usage
from crewbots.memory import CREW_SCOPE, Memory
from crewbots.runner import Runner
from crewbots.store import Status, Store

DECISIONS = {
    "notes": [
        {"op": "add", "title": "Exports page at 500 rows", "body": "The reports API pages exports at 500 rows."},
        {"op": "update", "name": "uses-pnpm", "body": "pnpm, never npm: npm breaks pnpm-lock.yaml."},
        {"op": "supersede", "name": "deploys-on-fridays", "title": "Deploys on Tuesdays", "body": "Since October."},
        {"op": "delete", "name": "old-pitfall"},
        {"op": "promote", "name": "user-likes-short-replies"},
        {"op": "delete", "name": "not-a-note"},
    ],
    "core": "The user ships on Tuesdays and likes short replies.",
    "summary": "Kept the export limit, moved deploys to Tuesdays.",
}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path)
    for title, body in (("Uses pnpm", "pnpm, not npm."), ("Deploys on Fridays", "Fridays at 4."),
                        ("Old pitfall", "Gone now."), ("User likes short replies", "Keep it brief.")):
        m.remember("engineer", title, body, "t_1")
    m.remember("critic", "User likes short replies", "Brief, please.", "t_2")
    m.log("engineer", "t_3 Add CSV export: done")
    return m


def runner_for(store, tmp_path, reply):
    backend = FakeBackend(reply=reply)
    crew = make_crew(tmp_path, bot("engineer"), bot("critic"))
    return Runner(store, crew, {"fake": backend}), backend


def answering(decisions):
    return lambda spec: [Usage(input_tokens=900, output_tokens=120), Done(final_text=f"```json\n{json.dumps(decisions)}\n```")]


def test_a_superseded_note_stays_but_drops_out_and_a_promoted_one_moves_to_the_crew(memory, tmp_path):
    new = memory.remember("engineer", "Deploys on Tuesdays", "Since October.", "t_4")
    old = memory.supersede("engineer", "deploys-on-fridays", new.name)
    assert old.superseded_by == "deploys-on-tuesdays" and old.path.exists() and "valid_until" in old.path.read_text()
    assert "deploys-on-fridays" not in [n.name for n in memory.notes("engineer")]
    assert "Deploys on Fridays" not in (tmp_path / "memory" / "engineer" / "MEMORY.md").read_text()
    assert len(memory.notes("engineer", superseded=True)) == 5

    memory.promote("engineer", "user-likes-short-replies")
    assert [n.title for n in memory.notes(CREW_SCOPE)] == ["User likes short replies"]
    assert "user-likes-short-replies" not in [n.name for n in memory.notes("engineer")]


def test_the_memory_folder_is_its_own_repository_with_a_commit_per_change(memory, tmp_path):
    first = memory.commit("Consolidated engineer's memory")
    assert first and memory.commit("nothing new") is None
    memory.write_core("engineer", "x" * 9000)
    assert memory.commit("Rewrote CORE.md") and len((tmp_path / "memory" / "engineer" / "CORE.md").read_text()) == 6001
    log = subprocess.run([paths.git(), "-C", str(tmp_path / "memory"), "log", "--format=%s %an"], capture_output=True,
                         text=True).stdout.splitlines()
    assert log == ["Rewrote CORE.md crewbots", "Consolidated engineer's memory crewbots"]


async def test_a_bot_consolidates_its_memory_and_crewbots_applies_what_it_decided(store, tmp_path, memory):
    runner, backend = runner_for(store, tmp_path, answering(DECISIONS))
    task = runner.dream("engineer", weekly=False)
    await wait_until(lambda: store.get_task(task.id).status is not Status.QUEUED and task.id not in runner.running())

    done = store.get_task(task.id)
    assert done.status is Status.IDLE and done.created_by == "crewbots" and done.parent_id is None
    assert done.result == ("Kept the export limit, moved deploys to Tuesdays. (1 add, 1 update, 1 supersede, 1 delete, "
                           "1 promote, CORE.md rewritten)")
    names = {n.name for n in memory.notes("engineer")}
    assert names == {"exports-page-at-500-rows", "uses-pnpm", "deploys-on-tuesdays"}
    assert {n.name: n.superseded_by for n in memory.notes("engineer", superseded=True)}["deploys-on-fridays"] == \
        "deploys-on-tuesdays"
    assert next(n for n in memory.notes("engineer") if n.name == "uses-pnpm").body.startswith("pnpm, never npm")
    assert [n.name for n in memory.notes(CREW_SCOPE)] == ["user-likes-short-replies"]
    assert memory.core("engineer") == "The user ships on Tuesdays and likes short replies."
    consolidated = next(e.data for e in store.events(task.id) if e.kind == "consolidated")
    assert consolidated["commit"] and len(consolidated["skipped"]) == 1
    assert consolidated["skipped"][0].startswith("delete not-a-note: ")  # a note that isn't there is skipped
    assert store.bot_usage("engineer", "2000-01-01") == 1020  # what it cost counts, like any turn

    spec = backend.specs[0]  # read-only, no tools, the bot's own model: it decides, crewbots writes
    assert spec.access.value == "read_only" and spec.mcp_servers == {} and "consolidating" in spec.system_prompt
    assert "t_3 Add CSV export: done" in spec.prompt and "critic/user-likes-short-replies" in spec.prompt
    assert "Deploys on Fridays" in spec.prompt  # changed since the last run (it never ran): in full


async def test_a_reply_that_isnt_json_changes_nothing(store, tmp_path, memory):
    runner, _ = runner_for(store, tmp_path, lambda spec: [Done(final_text="I'd rather not.")])
    task = runner.dream("engineer", weekly=False)
    await wait_until(lambda: task.id not in runner.running())
    assert store.get_task(task.id).status is Status.FAILED and "no JSON" in store.get_task(task.id).error
    assert len(memory.notes("engineer")) == 4 and memory.core("engineer") is None


async def test_it_runs_once_a_night_for_a_bot_with_something_new_when_the_crew_is_idle(store, tmp_path, memory):
    runner, _ = runner_for(store, tmp_path, answering({"notes": [], "summary": "Nothing to change."}))
    night, evening = datetime(2026, 10, 27, 3, 30).astimezone(), datetime(2026, 10, 26, 22, 0).astimezone()
    runner._turn_ended["t_x"] = time.time()  # the user was just busy
    assert not runner.dream_due("engineer", evening)
    runner._turn_ended["t_x"] = time.time() - 3700  # an hour's quiet: a Mac asleep at night gets it then
    assert runner.dream_due("engineer", evening)
    assert runner.dream_due("engineer", night) and runner.dream_due("critic", night)
    runner.limits["fake"] = {"seven_day": RateLimit(plan="fake", window="seven_day", status="warning")}
    assert not runner.dream_due("engineer", night)  # close to a limit: it waits
    runner.limits.clear()

    task = runner.dream("engineer", weekly=False)
    assert not runner.dream_due("critic", night)  # one bot at a time
    await wait_until(lambda: task.id not in runner.running())
    assert store.routine_state("memory:engineer")["last_run_at"]
    store.routine_ran("memory:engineer", datetime(2026, 10, 26, 23, 30).astimezone().isoformat(), task.id)  # in a quiet evening
    assert not runner.dream_due("engineer", night)  # done this night, early hours and all
    assert runner.dream_due("engineer", night + timedelta(days=1))


def test_the_app_or_the_cli_can_ask_for_it_now(store, tmp_path, memory):
    runner, _ = runner_for(store, tmp_path, answering({"notes": [], "summary": "Nothing to change."}))
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        auth = {"Authorization": "Bearer t"}
        task = client.post("/memory/consolidate", json={"bot": "engineer"}, headers=auth)
        assert task.status_code == 201 and task.json()["title"].startswith("Consolidate engineer's memory")
        assert client.post("/memory/consolidate", json={"bot": "ghost"}, headers=auth).status_code == 422
