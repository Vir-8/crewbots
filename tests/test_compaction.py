"""Keeping a long conversation in shape: flushing it to memory and compacting it in place, carrying on in a fresh
session when it can't be compacted or sat idle past its cache, and flushing ahead of time while the cache is warm."""

import time

import pytest
from fakes import bot, make_crew, wait_until
from test_sessions import FakeSession, SessionBackend

from crewbots import prompts
from crewbots.crew import NO_REPLY
from crewbots.events import Compacted, Done, SessionStarted
from crewbots.runner import Runner
from crewbots.store import Store

FLUSH = prompts.text("flush", no_reply=NO_REPLY)


class CompactingSession(FakeSession):
    """A session that reports how full it is, answers a flush with log lines, and compacts when asked."""

    async def context_percent(self):
        return self.backend.percent

    async def turn(self, prompt: str):
        if prompt == FLUSH:
            self.turns.append("<flush>")
            yield Done(final_text=self.backend.flush_reply)
            return
        async for event in super().turn(prompt):
            if self.backend.auto_compact and isinstance(event, Done):
                yield Compacted(trigger="auto", before=170_000, after=9_000)
            yield event

    async def compact(self, instructions: str):
        self.turns.append("<compact>")
        self.backend.percent = 12.0
        if self.backend.compactions_fail:
            yield Done(final_text="", is_error=True, error="compaction failed")
            return
        yield SessionStarted(session_id=self.session_id)
        yield Compacted(trigger="manual", before=140_000, after=8_000)
        yield Done(final_text="")


class CompactingBackend(SessionBackend):
    def __init__(self, percent=70.0):
        super().__init__()
        self.percent = percent
        self.flush_reply = "- Added the CSV export\n- The reports API pages at 500 rows"
        self.compactions_fail = False
        self.auto_compact = False

    async def open(self, spec):
        session = CompactingSession(self, spec)
        self.opened.append(session)
        return session


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def make(store, tmp_path, backend, **kw):
    runner = Runner(store, make_crew(tmp_path, bot()), {"fake": backend}, api_url="http://127.0.0.1:8765/v1", **kw)
    return runner


def idle(store, runner, task_id):
    return lambda: not store.get_task(task_id).active and task_id not in runner.running()


def kinds(store, task_id):
    return [e.kind for e in store.events(task_id) if e.kind in ("flushed", "compacted", "fresh_session", "rollover")]


async def test_a_full_conversation_is_flushed_to_memory_then_compacted_in_place(store, tmp_path):
    backend = CompactingBackend(percent=70.0)
    runner = make(store, tmp_path, backend)
    task = runner.submit("worker", "first")
    await wait_until(idle(store, runner, task.id))

    session = backend.opened[0]
    assert session.turns == ["first", "<flush>", "<compact>"] and not session.closed  # same session, compacted
    assert kinds(store, task.id) == ["flushed", "compacted"]
    assert store.get_task(task.id).result == "did: first"  # the hidden turns never replace the reply
    log = runner.crew.memory.recent("worker")
    assert f"{task.id} Added the CSV export" in log and f"{task.id} The reports API pages at 500 rows" in log

    runner.crew.memory.set_state("worker", task.id, "Goal: CSV export. PR acme/web#412.")
    runner.send(task.id, "second")  # told its instructions and state again, after the compaction
    await wait_until(lambda: len(session.turns) == 4 and idle(store, runner, task.id)())
    assert session.turns[3].startswith("<crew-updates>") and "## Your role card" in session.turns[3]
    assert "## Where this task stands\n\nGoal: CSV export. PR acme/web#412." in session.turns[3]
    assert session.turns[3].rstrip().endswith("second")


async def test_short_conversations_are_left_alone_and_nothing_new_logs_nothing(store, tmp_path):
    backend = CompactingBackend(percent=40.0)
    runner = make(store, tmp_path, backend)
    task = runner.submit("worker", "x")
    await wait_until(idle(store, runner, task.id))
    assert backend.opened[0].turns == ["x"] and kinds(store, task.id) == []

    backend.percent, backend.flush_reply = 80.0, NO_REPLY
    runner.send(task.id, "y")
    await wait_until(lambda: len(backend.opened[0].turns) == 4 and idle(store, runner, task.id)())
    assert runner.crew.memory.recent("worker") is None and kinds(store, task.id) == ["flushed", "compacted"]


async def test_a_conversation_that_wont_compact_carries_on_in_a_fresh_session(store, tmp_path):
    backend = CompactingBackend(percent=90.0)
    backend.compactions_fail = True
    runner = make(store, tmp_path, backend)
    task = runner.submit("worker", "plan the export")
    await wait_until(idle(store, runner, task.id))
    assert backend.opened[0].turns == ["plan the export", "<flush>", "<compact>", "<compact>"]  # tried twice
    assert backend.opened[0].closed and kinds(store, task.id) == ["flushed", "fresh_session"]

    backend.compactions_fail, backend.percent = False, 10.0
    runner.send(task.id, "carry on")
    await wait_until(lambda: len(backend.opened) == 2 and idle(store, runner, task.id)())
    fresh = backend.opened[1].spec
    assert fresh.resume_session_id is None and "couldn't be compacted" in fresh.system_prompt
    assert "[user]: plan the export" in fresh.system_prompt and "[worker]: did: plan the export" in fresh.system_prompt


async def test_when_the_cli_compacts_as_the_flush_begins_crewbots_doesnt_compact_again(store, tmp_path):
    backend = CompactingBackend(percent=95.0)
    runner = make(store, tmp_path, backend)
    session_turn = CompactingSession.turn

    async def flush_after_compacting(self, prompt):
        if prompt == FLUSH:  # the CLI compacts first, then the flush runs on what's left
            self.backend.percent = 15.0
            yield Compacted(trigger="auto", before=96_000, after=9_000)
        async for event in session_turn(self, prompt):
            yield event

    CompactingSession.turn = flush_after_compacting
    try:
        task = runner.submit("worker", "one huge turn")
        await wait_until(idle(store, runner, task.id))
    finally:
        CompactingSession.turn = session_turn
    assert backend.opened[0].turns == ["one huge turn", "<flush>"]  # no second compaction
    assert kinds(store, task.id) == ["compacted", "flushed"] and store.primed(task.id) == {}


async def test_after_the_cli_compacts_on_its_own_the_next_turn_hears_everything_again(store, tmp_path):
    backend = CompactingBackend(percent=20.0)
    backend.auto_compact = True
    runner = make(store, tmp_path, backend)
    task = runner.submit("worker", "first")
    await wait_until(idle(store, runner, task.id))
    backend.auto_compact = False
    runner.send(task.id, "second")
    await wait_until(lambda: len(backend.opened[0].turns) == 2 and idle(store, runner, task.id)())

    assert kinds(store, task.id) == ["compacted"]
    assert backend.opened[0].turns[1].startswith("<crew-updates>") and "## Your role card" in backend.opened[0].turns[1]


async def test_a_big_conversation_idle_past_its_cache_starts_afresh_and_a_small_one_resumes(store, tmp_path):
    backend = CompactingBackend(percent=50.0)
    runner = make(store, tmp_path, backend, cold_s=3600.0)
    big = runner.submit("worker", "the big one")
    await wait_until(idle(store, runner, big.id))
    runner._turn_ended[big.id] = time.time() - 3700  # as if an hour and more went by
    runner.send(big.id, "back again")
    await wait_until(lambda: len(backend.opened) == 2 and idle(store, runner, big.id)())
    fresh = backend.opened[1].spec
    assert fresh.resume_session_id is None and "sat idle for over an hour" in fresh.system_prompt
    assert kinds(store, big.id) == ["fresh_session"]

    backend.percent = 10.0
    small = runner.submit("worker", "a small one")
    await wait_until(idle(store, runner, small.id))
    session = backend.opened[2]
    runner._turn_ended[small.id] = time.time() - 3700
    runner.send(small.id, "back again")
    await wait_until(lambda: len(session.turns) == 2 and idle(store, runner, small.id)())
    assert len(backend.opened) == 3 and not session.closed and kinds(store, small.id) == []  # it carried on as it was


async def test_a_big_conversation_is_flushed_ahead_of_time_while_its_cache_is_warm(store, tmp_path):
    backend = CompactingBackend(percent=50.0)
    runner = make(store, tmp_path, backend, flush_idle_s=3000.0)
    task = runner.submit("worker", "the big one")
    await wait_until(idle(store, runner, task.id))
    runner._turn_ended[task.id] = time.time() - 3100  # 51 minutes on

    await runner._flush_idle()
    runner.send(task.id, "meanwhile")  # waits for the flush, then runs
    await wait_until(lambda: len(backend.opened[0].turns) == 3 and idle(store, runner, task.id)())
    await runner._flush_idle()  # once per idle spell: nothing more until its next turn

    first, flush, after = backend.opened[0].turns
    assert (first, flush) == ("the big one", "<flush>")
    assert "## Lately" in after and "Added the CSV export" in after and after.endswith("meanwhile")
    assert kinds(store, task.id) == ["flushed"] and "Added the CSV export" in runner.crew.memory.recent("worker")
