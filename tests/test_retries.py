"""Turns that fail for a passing reason are tried again; ones that would fail the same way are not."""

import asyncio
import time
from dataclasses import replace

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock
from fakes import FakeBackend, make_crew, wait_until

from crewbots import prompts
from crewbots.backends.base import claude_retry, codex_retry
from crewbots.backends.claude_sdk import SdkMapper
from crewbots.backends.codex_app_server import AppServerParser
from crewbots.events import Done, Notice, RateLimit, SessionStarted, Text
from crewbots.runner import Runner, holds
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def runner_for(store, tmp_path, backend, **kw):
    return Runner(store, make_crew(tmp_path), {"fake": backend}, worktree_root=tmp_path / "wts", **kw)


def settled(store, task_id):
    return lambda: store.get_task(task_id).status not in (Status.QUEUED, Status.RUNNING, Status.HELD)


def failing(times, retry="transient", session=True):
    """A backend whose first `times` turns fail with `retry`, and whose turns after that succeed."""
    calls = []

    def reply(spec):
        calls.append(spec)
        start = [SessionStarted(session_id="s-1")] if session else []
        if len(calls) <= times:
            return [*start, Done(final_text="", is_error=True, error="API Error: 529 overloaded", retry=retry)]
        return [*start, Text(text="done"), Done(final_text="done")]

    return FakeBackend(reply=reply)


async def test_a_turn_that_failed_for_a_passing_reason_carries_on_where_it_stopped(store, tmp_path):
    backend = failing(1)
    runner = runner_for(store, tmp_path, backend)

    task = runner.submit("worker", "build the export")
    await wait_until(settled(store, task.id))

    assert store.get_task(task.id).status is Status.IDLE and store.get_task(task.id).result == "done"
    first, retry = [s.prompt for s in backend.specs]
    assert first == "build the export"
    assert "## Working state" in retry and retry.endswith(prompts.text("retry", error="API Error: 529 overloaded"))
    assert backend.specs[1].resume_session_id == "s-1"
    notices = [e.data["message"] for e in store.events(task.id) if e.kind == "notice"]
    assert notices == ["Trying again in 0s (try 2 of 4)."]


async def test_without_a_session_the_turn_is_asked_again_as_it_was(store, tmp_path):
    backend = failing(1, session=False)
    runner = runner_for(store, tmp_path, backend)

    task = runner.submit("worker", "build the export")
    await wait_until(settled(store, task.id))

    assert [s.prompt for s in backend.specs] == ["build the export", "build the export"]


async def test_it_gives_up_after_the_last_try(store, tmp_path):
    backend = failing(10)
    runner = runner_for(store, tmp_path, backend)

    task = runner.submit("worker", "x")
    await wait_until(settled(store, task.id))

    assert len(backend.specs) == 4
    assert store.get_task(task.id).status is Status.FAILED
    assert store.get_task(task.id).error == "API Error: 529 overloaded"


async def test_a_failure_that_would_happen_again_is_not_retried(store, tmp_path):
    backend = failing(1, retry=None)
    runner = runner_for(store, tmp_path, backend)

    task = runner.submit("worker", "x")
    await wait_until(settled(store, task.id))

    assert len(backend.specs) == 1 and store.get_task(task.id).status is Status.FAILED


async def test_a_turn_that_goes_quiet_is_stopped_and_tried_again(store, tmp_path):
    class Stuck(FakeBackend):
        async def run(self, spec):
            self.specs.append(spec)
            yield SessionStarted(session_id="s-1")
            if len(self.specs) == 1:
                await asyncio.Event().wait()  # never answers
            yield Done(final_text="done")

    backend = Stuck()
    runner = runner_for(store, tmp_path, backend, stall_s=0.05)

    task = runner.submit("worker", "x")
    await wait_until(settled(store, task.id))

    assert store.get_task(task.id).status is Status.IDLE and len(backend.specs) == 2
    assert any("nothing came back from fake for 0.05s" in e.data.get("error", "") for e in store.events(task.id)
               if e.kind == "done")


async def test_a_turn_stopped_by_the_plans_limit_goes_on_when_it_resets(store, tmp_path, monkeypatch):
    monkeypatch.setattr(holds, "HOLD_RECHECK_S", 0.05)
    resets = int(time.time()) + 1
    calls = []

    def reply(spec):
        calls.append(spec)
        if len(calls) == 1:
            return [SessionStarted(session_id="s-1"),
                    RateLimit(plan="fake", window="five_hour", status="limited", resets_at=resets),
                    Done(final_text="", is_error=True, error="usage limit reached", retry="limit")]
        return [Done(final_text="done")]

    backend = FakeBackend(reply=reply)
    runner = runner_for(store, tmp_path, backend)

    task = runner.submit("worker", "x")
    await wait_until(lambda: store.get_task(task.id).status is Status.HELD)
    await wait_until(settled(store, task.id), timeout=5)

    assert store.get_task(task.id).status is Status.IDLE and time.time() >= resets
    assert [e.data["message"] for e in store.events(task.id) if e.kind == "notice"] == [
        "Trying again when the limit resets (try 2 of 4)."]


async def test_cancelling_while_it_waits_to_try_again_stops_it(store, tmp_path):
    backend = failing(1)
    runner = runner_for(store, tmp_path, backend, retry_waits_s=(60.0,))

    task = runner.submit("worker", "x")
    await wait_until(lambda: store.get_task(task.id).status is Status.QUEUED and len(backend.specs) == 1)
    await runner.cancel(task.id)

    assert store.get_task(task.id).status is Status.CANCELLED and len(backend.specs) == 1


# Which failures each backend calls passing


def test_claude_failures_are_judged_by_the_api_status_and_error_kind():
    assert claude_retry(529, None) == claude_retry(None, "server_error") == "transient"
    assert claude_retry(429, None) == claude_retry(None, "rate_limit") == "limit"
    assert claude_retry(400, "invalid_request") is None and claude_retry(None, "authentication_failed") is None


def test_the_agent_sdk_turn_that_gave_up_on_an_overloaded_api_is_passing():
    mapper = SdkMapper()
    events = [e for m in [
        AssistantMessage(content=[TextBlock(text="API Error: 529 Overloaded")], model="m", error="server_error"),
        ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1, session_id="s",
                      result="API Error: 529 Overloaded", api_error_status=529),
    ] for e in mapper.feed(m)]

    assert events[-1] == Done(final_text="API Error: 529 Overloaded", is_error=True, error="API Error: 529 Overloaded",
                              retry="transient")


def test_codex_failures_are_judged_by_their_error_kind():
    def failed(info):
        turn = {"id": "t", "status": "failed", "error": {"message": "boom", "codexErrorInfo": info}}
        return AppServerParser().feed("turn/completed", {"turn": turn}, "t")[-1].retry

    assert failed({"responseStreamDisconnected": {"httpStatusCode": 502}}) == "transient"
    assert failed("serverOverloaded") == "transient" and failed("usageLimitExceeded") == "limit"
    assert failed("contextWindowExceeded") is None and failed("unauthorized") is None and failed(None) is None
    assert codex_retry("rateLimitExceeded") == "limit"
    assert [type(e) for e in AppServerParser().feed("error", {"error": {"message": "retrying"}, "willRetry": True})] == [
        Notice]


def resume_fails(error):
    """A backend that can't resume the stored session: it says `error` while asked to, and starts afresh otherwise."""
    calls = []

    def reply(spec):
        calls.append(spec)
        if spec.resume_session_id:
            return [Done(final_text="", is_error=True, error=error, retry="transient")]
        return [SessionStarted(session_id="s-new"), Text(text="done"), Done(final_text="done")]

    return FakeBackend(reply=reply)


@pytest.mark.parametrize("error", ["codex app-server: thread/resume: no rollout found for thread id 9869b148",
                                   "No conversation found with session ID 01a17e6b"])
async def test_a_session_that_is_gone_starts_one_fresh_session_at_once(store, tmp_path, error):
    backend = resume_fails(error)
    runner = runner_for(store, tmp_path, backend)
    task = runner.submit("worker", "first")
    await wait_until(settled(store, task.id))
    store.update_task(task.id, session_id="stale", session_backend="fake")
    backend.specs.clear()

    runner.send(task.id, "the pending message")
    await wait_until(lambda: len(backend.specs) >= 2 and settled(store, task.id)())

    resumed, fresh = backend.specs
    assert resumed.resume_session_id == "stale" and fresh.resume_session_id is None
    assert fresh.prompt.endswith("the pending message") and "Trying again" not in fresh.prompt
    after = store.get_task(task.id)
    assert after.status is Status.IDLE and after.session_id == "s-new" and after.session_backend == "fake"
    notices = [e.data["message"] for e in store.events(task.id) if e.kind == "notice"]
    assert not any(m.startswith("Trying again") for m in notices)
    assert any(m.startswith("Starting a fresh session") for m in notices)


async def test_a_session_from_another_backend_is_not_resumed(store, tmp_path):
    backend = failing(0)
    runner = runner_for(store, tmp_path, backend)
    task = runner.submit("worker", "first")
    await wait_until(settled(store, task.id))
    store.update_task(task.id, session_id="codex-thread", session_backend="codex")
    backend.specs.clear()

    runner.send(task.id, "again")
    await wait_until(lambda: backend.specs and settled(store, task.id)())

    assert [s.resume_session_id for s in backend.specs] == [None]
    assert store.get_task(task.id).session_backend == "fake"


async def test_other_resume_errors_are_still_retried(store, tmp_path):
    backend = resume_fails("API Error: 529 overloaded")
    runner = runner_for(store, tmp_path, backend)
    task = runner.submit("worker", "first")
    await wait_until(settled(store, task.id))
    store.update_task(task.id, session_id="stale", session_backend="fake")
    backend.specs.clear()

    runner.send(task.id, "again")
    await wait_until(settled(store, task.id))

    assert store.get_task(task.id).status is Status.FAILED
    assert len(backend.specs) == 4 and all(s.resume_session_id == "stale" for s in backend.specs)


def room_seat(store, tmp_path, backend, waits):
    runner = runner_for(store, tmp_path, backend, retry_waits_s=waits)
    room = runner.create_room("Engineering", ["worker"])
    return runner, room


def seat_updates(queue):
    out = []
    while not queue.empty():
        message = queue.get_nowait()
        if message["type"] == "task":
            out.append(message["task"])
    return out


async def test_a_seat_waiting_to_try_again_says_so_and_is_not_running(store, tmp_path):
    runner, room = room_seat(store, tmp_path, failing(10), (30.0, 30.0))
    with runner.bus.subscribe() as queue:
        runner.post(room["id"], "user", "@worker go")
        seat = lambda: store.seat_of(room["id"], "worker")  # noqa: E731
        await wait_until(lambda: seat() and runner.retrying(seat().id))

        assert seat().status is Status.QUEUED  # not running: nothing is being done
        assert runner.retrying(seat().id) == {"attempt": 2, "of": 3, "wait_seconds": 30.0}
        shown = [t for t in seat_updates(queue) if t["id"] == seat().id]
        assert shown[-1]["status"] == "queued" and shown[-1]["attempt"] == {"attempt": 2, "of": 3, "wait_seconds": 30.0}
        assert all(t["attempt"] is None for t in shown if t["status"] == "running")
    runner._runs[seat().id].cancel()


async def test_the_last_failed_try_clears_the_retry_and_tells_the_room_once(store, tmp_path):
    runner, room = room_seat(store, tmp_path, failing(10), (0.0, 0.0))
    runner.post(room["id"], "user", "@worker go")
    seat = lambda: store.seat_of(room["id"], "worker")  # noqa: E731
    await wait_until(lambda: seat() and seat().status is Status.FAILED)
    await asyncio.sleep(0.05)

    assert runner.retrying(seat().id) is None
    told = [m for m in store.room_messages(room["id"]) if m["author"] == "worker"]
    assert [m["text"] for m in told] == ["I couldn't take this turn: backend error. Details: room_status."]
    assert "529" in store.get_task(seat().id).error  # the error stays where room_status reads it


async def test_the_room_is_told_a_fixed_summary_never_the_error_text(store, tmp_path):
    secret = "Cookie: session=abc123 password=hunter2 sk-abcdefghijklmnop"
    backend = FakeBackend(reply=lambda spec: [Done(final_text="", is_error=True, error=secret)])
    runner, room = room_seat(store, tmp_path, backend, (0.0,))
    runner.post(room["id"], "user", "@worker go")
    await wait_until(lambda: any(m["author"] == "worker" for m in store.room_messages(room["id"])))

    told = [m["text"] for m in store.room_messages(room["id"]) if m["author"] == "worker"]
    assert told == ["I couldn't take this turn: unexpected error. Details: room_status."]
    assert not any(bit in told[0] for bit in ("abc123", "hunter2", "sk-abc", "Cookie"))


async def test_a_seat_that_cannot_start_tells_its_room_once(store, tmp_path):
    runner, room = room_seat(store, tmp_path, failing(0), (0.0,))
    runner.crew.bots["worker"] = replace(runner.crew.bots["worker"], workspace=tmp_path / "gone")
    runner.post(room["id"], "user", "@worker go")
    await wait_until(lambda: any(m["author"] == "worker" for m in store.room_messages(room["id"])))
    await asyncio.sleep(0.05)

    told = [m["text"] for m in store.room_messages(room["id"]) if m["author"] == "worker"]
    assert told == ["I couldn't take this turn: workspace missing. Details: room_status."]


async def test_a_failure_without_text_gets_the_fallback_summary(store, tmp_path):
    backend = FakeBackend(reply=lambda spec: [Done(final_text="", is_error=True, error=None)])
    runner, room = room_seat(store, tmp_path, backend, (0.0,))
    runner.post(room["id"], "user", "@worker go")
    await wait_until(lambda: any(m["author"] == "worker" for m in store.room_messages(room["id"])))

    told = [m["text"] for m in store.room_messages(room["id"]) if m["author"] == "worker"]
    assert told == ["I couldn't take this turn: unexpected error. Details: room_status."]


async def test_a_retried_turn_records_one_turn_start(store, tmp_path):
    runner = runner_for(store, tmp_path, failing(2))
    task = runner.submit("worker", "build the export")
    await wait_until(settled(store, task.id))
    assert [e.kind for e in store.events(task.id)].count("turn_start") == 1
