import asyncio
import json

import pytest
from fakes import FakeBackend, as_bot, bot, make_crew, start, wait_until

from crewbots.backends.base import BackendBase
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done
from crewbots.runner import Runner
from crewbots.runner.rooms import _safe_error
from crewbots.store import Status, Store


def test_error_redaction_and_limit():
    error = ('password="two words" token=abc Authorization: Bearer abc123 '
             'https://user:pass@example.invalid/?api_key=secret sk-abcdefghijklmnop ' + "x" * 2000)
    safe = _safe_error(error)
    for secret in ("two words", "abc123", "user:pass", "api_key=secret", "sk-abcdefghijklmnop"):
        assert secret not in safe
    assert len(safe) == 1001 and safe.endswith("…")


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("chief", can_delegate=True), bot("worker")),
                    {"fake": FakeBackend()}, retry_waits_s=(60, 60, 60))
    yield runner, store
    store.close()


async def test_room_status_authorization_and_error_recovery(setup):
    runner, store = setup
    own = runner.create_room("Engineering", ["chief", "worker"])
    other = runner.create_room("Private", ["worker"])
    seat = store.create_task("worker", "seat", kind="seat", room_id=own["id"], status=Status.FAILED)
    store.update_task(seat.id, error="failed: token=verysecret")
    tools = CrewTools(runner, runner.chat("chief").id, True)
    assert "room_status" in {tool.name for tool in tools.definitions()}
    data = json.loads(await tools.call("room_status", {}))
    assert [room["room_id"] for room in data["rooms"]] == [own["id"]]
    entry = data["rooms"][0]["seats"][0]
    assert (entry["task_id"], entry["bot"], entry["status"]) == (seat.id, "worker", "failed")
    assert entry["updated_at"] and entry["error"] == "failed: token=[redacted]"
    store.append_event(seat.id, Done(final_text="", is_error=True, error="previous failure password=hunter2"))
    store.update_task(seat.id, error=None)
    data = json.loads(await tools.call("room_status", {"room": own["id"]}))
    assert data["rooms"][0]["seats"][0]["error"] == "previous failure password=[redacted]"
    store.append_event(seat.id, Done(final_text="", is_error=False))  # a good turn since: the old error is history
    data = json.loads(await tools.call("room_status", {"room": own["id"]}))
    assert data["rooms"][0]["seats"][0]["error"] is None
    with pytest.raises(ToolRefused):
        await tools.call("room_status", {"room": other["id"]})
    worker = CrewTools(runner, runner.chat("worker").id, False)
    assert "room_status" not in {tool.name for tool in worker.definitions()}
    with pytest.raises(ToolRefused):
        await worker.call("room_status", {})


async def test_live_retry_state_and_cleanup(setup):
    runner, store = setup
    room = runner.create_room("Engineering", ["chief", "worker"])
    seat = store.create_task("worker", "seat", kind="seat", room_id=room["id"])
    runner.backends["fake"] = FakeBackend(reply=lambda _: [Done(final_text="", is_error=True,
                                                                 error="overloaded", retry="transient")])
    chief = runner.chat("chief").id

    def attempt():
        return runner.room_status(chief)["rooms"][0]["seats"][0]["attempt"]

    work = asyncio.create_task(runner._tries(seat.id, "test"))
    await wait_until(lambda: (attempt() or {}).get("state") == "backoff")
    assert attempt() == {"attempt": 2, "of": 4, "state": "backoff", "wait_seconds": 60}
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work
    assert attempt() is None


async def test_running_and_stalled_attempt(setup):
    runner, store = setup
    room = runner.create_room("Engineering", ["chief", "worker"])
    seat = store.create_task("worker", "seat", kind="seat", room_id=room["id"])

    class QuietBackend(BackendBase):
        async def run(self, spec):
            await asyncio.Event().wait()
            yield Done(final_text="")

    runner.backends["fake"] = QuietBackend()
    chief = runner.chat("chief").id

    def attempt():
        return runner.room_status(chief)["rooms"][0]["seats"][0]["attempt"]

    work = asyncio.create_task(runner._tries(seat.id, "test"))
    await wait_until(lambda: (attempt() or {}).get("state") == "running")
    assert attempt() == {"attempt": 1, "of": 4, "state": "running"}
    runner.stall_s = 0
    assert attempt()["state"] == "stalled"
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work


def test_bot_route_refuses_non_coordinator_and_nonmember(crew_app):
    client, backend, store = crew_app
    _, chief_token = start(client, backend, "chief")
    _, worker_token = start(client, backend, "engineer")
    own = store.create_room("Engineering", "chief", ["chief", "engineer"])
    other = store.create_room("Private", "engineer", ["engineer"])
    assert client.post("/bot/tools/room_status", json={}, headers=as_bot(worker_token)).status_code == 422
    assert client.post("/bot/tools/room_status", json={"room": other["id"]},
                       headers=as_bot(chief_token)).status_code == 422
    result = client.post("/bot/tools/room_status", json={"room": own["id"]}, headers=as_bot(chief_token))
    assert result.status_code == 200 and json.loads(result.json()["content"])["rooms"][0]["room_id"] == own["id"]
