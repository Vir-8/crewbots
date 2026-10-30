import json
import time

import pytest
from fakes import FakeBackend, bot, make_crew

from crewbots import timefmt, working_state
from crewbots.crew_tools import CrewTools
from crewbots.runner import Runner
from crewbots.store import Status, Store


@pytest.fixture(autouse=True)
def kolkata(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_local_minute_converts_utc_and_naive():
    assert timefmt.minute("2026-10-28T10:33:12.500+00:00") == "2026-10-28T16:03"
    assert timefmt.minute("2026-10-28T10:33") == "2026-10-28T16:03"
    assert timefmt.minute("2026-10-28T20:00:00+00:00") == "2026-10-29T01:30"


def test_needs_me_entry_is_local(tmp_path):
    path = tmp_path / "state.md"
    msg = {"id": 1, "author": "engineer", "at": "2026-10-28T10:33:00.000+00:00"}
    working_state.room_mentions(path, {"id": "r_abc", "title": "one"}, [msg], add=True)
    assert "2026-10-28T16:03 · engineer" in path.read_text()


def test_elsewhere_and_questions_are_local(tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("chief", can_delegate=True), bot("worker")), {"fake": FakeBackend()})
    chat = runner.chat("worker")
    store.db.execute("update tasks set updated_at = ? where id = ?", ("2026-10-28T10:33:00.000+00:00", chat.id))
    assert runner._sync_working_state("worker")
    text = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    assert f"your chat with the user ({chat.id}) · idle · 2026-10-28T16:03" in text
    store.close()


async def test_room_status_updated_at_is_local(tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("chief", can_delegate=True), bot("worker")), {"fake": FakeBackend()})
    room = runner.create_room("Engineering", ["chief", "worker"])
    seat = store.create_task("worker", "seat", kind="seat", room_id=room["id"], status=Status.FAILED)
    store.db.execute("update tasks set updated_at = ? where id = ?", ("2026-10-28T10:33:00.000+00:00", seat.id))
    data = json.loads(await CrewTools(runner, runner.chat("chief").id, True).call("room_status", {}))
    assert data["rooms"][0]["seats"][0]["updated_at"] == "2026-10-28T16:03"
    store.close()


def test_the_zone_defaults_to_the_machines_and_can_be_set(tmp_path):
    store = Store(tmp_path / "crew.db")
    assert store.timezone() is None and timefmt.zone_name() == "Asia/Kolkata" == timefmt.system_zone()
    store.set_timezone("America/New_York")
    assert timefmt.minute("2026-10-28T10:33:00+00:00") == "2026-10-28T06:33"
    assert Store(tmp_path / "crew.db").timezone() == "America/New_York"  # kept
    store.set_timezone(None)
    assert timefmt.zone_name() == "Asia/Kolkata"
    assert not timefmt.valid("Not/AZone")
    store.close()


async def test_every_turn_starts_with_the_header(tmp_path, monkeypatch):
    from fakes import REAL_WITH_UPDATES, wait_until

    monkeypatch.setattr(Runner, "_with_updates", REAL_WITH_UPDATES)
    store = Store(tmp_path / "crew.db")
    backend = FakeBackend()
    runner = Runner(store, make_crew(tmp_path, bot("worker")), {"fake": backend})
    task = runner.submit("worker", "hello")
    await wait_until(lambda: backend.specs)
    assert backend.specs[0].prompt.startswith("[crewbots: it is ") and "IST (Asia/Kolkata, UTC+05:30)" in backend.specs[0].prompt
    assert backend.specs[0].prompt.endswith("hello")
    await wait_until(lambda: store.get_task(task.id).status is Status.IDLE)
    store.close()


def test_recall_and_tasks_are_local(tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("chief", can_delegate=True), bot("worker")), {"fake": FakeBackend()})
    child = store.create_task("worker", "do it", parent_id=runner.chat("chief").id, created_by="chief")
    store.db.execute("update tasks set updated_at = ? where id = ?", ("2026-10-28T10:33:00.000+00:00", child.id))
    listed = runner.delegated_by(runner.chat("chief").id)
    assert listed[0]["updated_at"] == "2026-10-28T16:03:00+05:30"
    assert timefmt.stamp("2026-10-28T10:33:00+00:00") == "2026-10-28T16:03:00+05:30"
    assert timefmt.stamp("2026-10-28") == "2026-10-28"  # a memory log's day stays as it is
    store.close()
