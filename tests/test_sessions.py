"""Long-lived sessions in the runner: one per active task, reused across turns, closed when idle, cancelled or done."""

import asyncio

import pytest
from fakes import bot, make_crew, wait_until

from crewbots.backends import RunSpec
from crewbots.events import Done, SessionStarted, Text
from crewbots.runner import MCP_SERVER_NAME, Runner
from crewbots.store import Status, Store


class FakeSession:
    def __init__(self, backend, spec: RunSpec):
        self.backend = backend
        self.spec = spec
        self.session_id = spec.resume_session_id or f"s-{len(backend.opened)}"
        self.turns: list[str] = []
        self.closed = False
        self.die_after_turn = False

    @property
    def alive(self) -> bool:
        return not self.closed

    async def turn(self, prompt: str):
        self.turns.append(prompt)
        if self.backend.gate:
            await self.backend.gate.wait()
        yield SessionStarted(session_id=self.session_id)
        yield Text(text=f"did: {prompt}")
        yield Done(final_text=f"did: {prompt}")
        if self.die_after_turn:
            self.closed = True

    async def close(self):
        self.closed = True


class SessionBackend:
    name = "fake"

    def __init__(self, fail_open=False):
        self.opened: list[FakeSession] = []
        self.one_shot: list[RunSpec] = []
        self.fail_open = fail_open
        self.gate: asyncio.Event | None = None
        self.sessions_die = False

    async def open(self, spec: RunSpec) -> FakeSession:
        if self.fail_open:
            raise RuntimeError("no app-server here")
        session = FakeSession(self, spec)
        session.die_after_turn = self.sessions_die
        self.opened.append(session)
        return session

    async def run(self, spec: RunSpec):
        self.one_shot.append(spec)
        yield Done(final_text="one-shot")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def make(store, tmp_path, backend=None, *bots, **kw):
    backend = backend or SessionBackend()
    runner = Runner(store, make_crew(tmp_path, *bots), {"fake": backend}, api_url="http://127.0.0.1:8765/v1", **kw)
    return runner, backend


def settled(store, runner, task_id):
    return lambda: not store.get_task(task_id).active and task_id not in runner.running()


async def test_follow_ups_reuse_the_same_session(store, tmp_path):
    runner, backend = make(store, tmp_path)
    task = runner.submit("worker", "first")
    await wait_until(settled(store, runner, task.id))
    runner.send(task.id, "second")
    await wait_until(lambda: len(backend.opened[0].turns) == 2 and settled(store, runner, task.id)())

    assert len(backend.opened) == 1 and backend.opened[0].turns == ["first", "second"]
    assert backend.opened[0].spec.prompt == ""  # each turn brings its own prompt
    assert store.get_task(task.id).result == "did: second"
    assert backend.one_shot == []


async def test_a_session_token_works_while_the_session_is_open_and_not_after(store, tmp_path):
    runner, backend = make(store, tmp_path)
    task = runner.submit("worker", "x")
    await wait_until(settled(store, runner, task.id))
    token = backend.opened[0].spec.mcp_servers[MCP_SERVER_NAME].env["CREWBOTS_TOKEN"]

    assert runner.task_for_token(token).id == task.id  # between turns, still the task's
    await runner.cancel(task.id)
    assert runner.task_for_token(token) is None
    assert backend.opened[0].closed


async def test_an_idle_session_is_closed_and_the_next_message_resumes_it(store, tmp_path):
    runner, backend = make(store, tmp_path, idle_timeout_s=0.2)
    task = runner.submit("worker", "first")
    await wait_until(settled(store, runner, task.id))
    await wait_until(lambda: backend.opened[0].closed, timeout=5)

    assert runner.open_sessions() == []
    runner.send(task.id, "later")
    await wait_until(lambda: len(backend.opened) == 2 and settled(store, runner, task.id)())
    assert backend.opened[1].spec.resume_session_id == "s-0"


async def test_a_session_that_died_is_reopened_from_its_id(store, tmp_path):
    runner, backend = make(store, tmp_path)
    backend.sessions_die = True  # each session's process ends after its first turn
    task = runner.submit("worker", "first")
    await wait_until(settled(store, runner, task.id))

    runner.send(task.id, "again")
    await wait_until(lambda: len(backend.opened) == 2 and settled(store, runner, task.id)())

    assert backend.opened[1].spec.resume_session_id == backend.opened[0].session_id
    assert store.get_task(task.id).status is Status.IDLE


async def test_when_a_session_wont_open_the_turn_runs_on_its_own(store, tmp_path):
    runner, backend = make(store, tmp_path, SessionBackend(fail_open=True))
    task = runner.submit("worker", "x")
    await wait_until(settled(store, runner, task.id))

    assert store.get_task(task.id).result == "one-shot"
    assert any("couldn't keep a session open" in e.data.get("message", "") for e in store.events(task.id))


async def test_sessions_can_be_turned_off(store, tmp_path):
    runner, backend = make(store, tmp_path, sessions=False)
    task = runner.submit("worker", "x")
    await wait_until(settled(store, runner, task.id))

    assert backend.opened == [] and len(backend.one_shot) == 1


async def test_past_the_limit_the_least_recently_used_idle_session_is_closed(store, tmp_path):
    runner, backend = make(store, tmp_path, None, bot("a"), bot("b"), bot("c"), max_sessions=2)
    for name in ("a", "b", "c"):
        task = runner.submit(name, f"work for {name}")
        await wait_until(settled(store, runner, task.id))

    assert len(runner.open_sessions()) == 2
    assert backend.opened[0].closed and not backend.opened[2].closed


async def test_cancelling_mid_turn_closes_the_session(store, tmp_path):
    backend = SessionBackend()
    backend.gate = asyncio.Event()
    runner, _ = make(store, tmp_path, backend)
    task = runner.submit("worker", "forever")
    await wait_until(lambda: len(backend.opened) == 1 and backend.opened[0].turns)

    await runner.cancel(task.id)

    assert backend.opened[0].closed and runner.open_sessions() == []
    assert store.get_task(task.id).status is Status.CANCELLED


async def test_shutdown_closes_every_session(store, tmp_path):
    runner, backend = make(store, tmp_path, None, bot("a"), bot("b"))
    for name in ("a", "b"):
        task = runner.submit(name, "x")
        await wait_until(settled(store, runner, task.id))

    await runner.shutdown()

    assert all(s.closed for s in backend.opened) and runner.open_sessions() == []


class InProcessBackend(SessionBackend):
    """Like the Agent SDK backend: hosts the crew tools itself, and its sessions can stop a turn and stay open."""

    in_process_tools = True

    async def open(self, spec):
        session = await super().open(spec)
        session.interruptible = True
        return session


async def test_an_in_process_backend_gets_the_crew_tools_as_python_not_a_server(store, tmp_path):
    from crewbots.crew_tools import CrewTools

    runner, backend = make(store, tmp_path, InProcessBackend(), bot("chief", can_delegate=True))
    task = runner.submit("chief", "x")
    await wait_until(settled(store, runner, task.id))

    spec = backend.opened[0].spec
    assert isinstance(spec.crew, CrewTools) and spec.crew.task_id == task.id and spec.crew.can_delegate
    assert MCP_SERVER_NAME not in spec.mcp_servers


async def test_cancelling_an_interruptible_session_keeps_it_warm(store, tmp_path):
    backend = InProcessBackend()
    backend.gate = asyncio.Event()
    runner, _ = make(store, tmp_path, backend)
    task = runner.submit("worker", "forever")
    await wait_until(lambda: len(backend.opened) == 1 and backend.opened[0].turns)

    await runner.cancel(task.id)
    backend.gate.set()
    runner.send(task.id, "do something else")
    await wait_until(lambda: len(backend.opened[0].turns) == 2 and settled(store, runner, task.id)())

    assert len(backend.opened) == 1 and not backend.opened[0].closed  # the same session took the next turn
