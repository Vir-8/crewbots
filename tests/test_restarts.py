"""A restart in the middle of the crew's work: what was cut off, or waiting its turn, carries on once by itself."""

import asyncio

import pytest
from fakes import FakeBackend, bot, make_crew, wait_until

from crewbots.crew import Budget
from crewbots.runner import Runner
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def daemon(store, tmp_path, *bots, gate=None, **kw):
    """One run of the daemon over the crew's database: a fresh runner, as a restart makes."""
    backend = FakeBackend(gate=gate)
    return Runner(store, make_crew(tmp_path, *bots), {"fake": backend}, sessions=False, **kw), backend


def settled(store, runner, task_id):
    return lambda: not store.get_task(task_id).active and task_id not in runner.running()


def kinds(store, task_id):
    return [e.kind for e in store.events(task_id)]


async def test_a_turn_cut_off_by_a_stop_carries_on_after_the_restart(store, tmp_path):
    first, backend = daemon(store, tmp_path, gate=asyncio.Event())
    task = first.submit("worker", "write the report")
    await wait_until(lambda: backend.active == 1)
    first.send(task.id, "and add a chart")  # arrives while it works
    await first.shutdown()
    assert store.get_task(task.id).status == Status.INTERRUPTED

    second, backend = daemon(store, tmp_path)
    store.mark_interrupted()
    assert second.resume_interrupted() == [task.id]
    await wait_until(settled(store, second, task.id))

    [prompt] = [s.prompt for s in backend.specs]
    assert "daemon restarted while you were working on this" in prompt and "write the report" in prompt
    assert prompt.endswith("and add a chart")  # what came in meanwhile isn't lost
    assert store.get_task(task.id).status == Status.IDLE
    assert "restarted" in kinds(store, task.id)  # the thread says so
    assert not store.interrupted_turns() and second.resume_interrupted() == []  # once


async def test_a_crash_is_carried_on_too(store, tmp_path):
    # Killed outright: nothing marks the task, so the next start finds it still running.
    first, backend = daemon(store, tmp_path, gate=asyncio.Event())
    task = first.submit("worker", "write the report")
    await wait_until(lambda: store.get_task(task.id).status == Status.RUNNING)

    second, backend = daemon(store, tmp_path)
    assert store.mark_interrupted() == [task.id]
    assert second.resume_interrupted() == [task.id]
    await wait_until(settled(store, second, task.id))
    assert "write the report" in backend.specs[0].prompt
    for run in list(first._runs.values()):
        run.cancel()


async def test_a_turn_still_waiting_its_turn_starts_as_it_was(store, tmp_path):
    first, backend = daemon(store, tmp_path, gate=asyncio.Event(), max_parallel=1)
    busy = first.submit("worker", "first")
    waiting = first.submit("worker", "second")
    await wait_until(lambda: backend.active == 1)
    await first.shutdown()

    second, backend = daemon(store, tmp_path)
    assert sorted(second.resume_interrupted()) == sorted([busy.id, waiting.id])
    await wait_until(settled(store, second, waiting.id))

    assert "second" in [s.prompt for s in backend.specs]  # never started, so it's asked as it was
    assert "restarted" not in kinds(store, waiting.id)


async def test_a_cancelled_task_is_not_carried_on(store, tmp_path):
    first, backend = daemon(store, tmp_path, gate=asyncio.Event())
    task = first.submit("worker", "write the report")
    await wait_until(lambda: backend.active == 1)
    await first.cancel(task.id)
    await first.shutdown()

    second, _ = daemon(store, tmp_path)
    assert second.resume_interrupted() == []
    assert store.get_task(task.id).status == Status.CANCELLED


async def test_carried_on_once_per_interruption(store, tmp_path):
    first, backend = daemon(store, tmp_path, gate=asyncio.Event())
    task = first.submit("worker", "write the report")
    await wait_until(lambda: backend.active == 1)
    await first.shutdown()

    second, backend = daemon(store, tmp_path, gate=asyncio.Event())
    second.resume_interrupted()
    await wait_until(lambda: backend.active == 1)
    await second.shutdown()  # cut off again while carrying on

    third, backend = daemon(store, tmp_path)
    assert third.resume_interrupted() == []
    assert store.get_task(task.id).status == Status.INTERRUPTED and not backend.specs
    again = [e for e in store.events(task.id) if e.kind == "restarted"][-1]
    assert again.data == {"again": True}  # the thread asks for a message to carry on
    assert third.resume_interrupted() == []  # and says it only once


async def test_a_held_task_stays_held_after_a_restart(store, tmp_path):
    over = bot(budget=Budget(tokens=10, per="day"))
    first, _ = daemon(store, tmp_path, over)
    task = first.submit("worker", "one")
    await wait_until(settled(store, first, task.id))
    first.send(task.id, "two")  # past its budget now: held
    await wait_until(lambda: store.get_task(task.id).status == Status.HELD)
    await first.shutdown()
    assert store.mark_interrupted() == []  # shutdown marked it already

    second, backend = daemon(store, tmp_path, over)
    second.resume_interrupted()
    await wait_until(lambda: store.get_task(task.id).status == Status.HELD)
    assert not backend.specs

    second.release(task.id)
    await wait_until(settled(store, second, task.id))
    assert backend.specs[0].prompt == "two"
    await second.shutdown()
