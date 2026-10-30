import asyncio
import atexit
import functools
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from crewbots import paths
from crewbots.backends import Access, RunSpec
from crewbots.backends.base import BackendBase, SessionBase
from crewbots.crew import Bot, Crew
from crewbots.events import Done, SessionStarted, Text, Usage
from crewbots.runner import MCP_SERVER_NAME, Runner

# The turn header carries the clock, so it would put a different first line in every prompt the tests compare. Tests of
# the header put `REAL_WITH_UPDATES` back with monkeypatch.
REAL_WITH_UPDATES = Runner._with_updates
Runner._with_updates = Runner._updates


def reply(spec: RunSpec):
    """A run that says back what it was asked."""
    return [
        SessionStarted(session_id=spec.resume_session_id or "s-1"),
        Text(text=f"did: {spec.prompt}"),
        Usage(input_tokens=10, output_tokens=2, cost_usd=0.01),
        Done(final_text=f"did: {spec.prompt}"),
    ]


class FakeBackend(BackendBase):
    """Stands in for a CLI backend in-process. Set `gate` to hold every run until it is set."""

    def __init__(self, reply=reply, gate: asyncio.Event | None = None):
        self.reply = reply
        self.gate = gate
        self.specs: list[RunSpec] = []
        self.active = 0
        self.max_active = 0
        self.closed = 0  # runs whose stream was closed, finished or not

    async def run(self, spec: RunSpec):
        self.specs.append(spec)
        if spec.on_prompt_sent:
            spec.on_prompt_sent()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.gate:
                await self.gate.wait()
            for event in self.reply(spec):
                yield event
        finally:
            self.active -= 1
            self.closed += 1


def bot(name="worker", access=Access.READ_ONLY, workspace: Path | None = None, **kw) -> Bot:
    return Bot(name=name, title=name.title(), description=f"the {name}", instructions=f"You are the {name}.",
               backend="fake", access=access, workspace=workspace, **kw)


def make_crew(home: Path, *bots: Bot, team: dict[str, str] | None = None) -> Crew:
    bots = bots or (bot(),)
    return Crew(home=home, bots={b.name: b for b in bots}, team_files=team or {})


@functools.cache
def _first_commit() -> Path:
    """A repository with a README in one commit, made once: git_repo copies it, which takes a few milliseconds where
    git init and a commit take a tenth of a second."""
    path = Path(tempfile.mkdtemp(prefix="crewbots-tests-repo-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    run = lambda *a: subprocess.run([paths.git(), *a], cwd=path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    (path / "README.md").write_text("hello\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return path


def git_repo(path: Path) -> Path:
    """A repository at `path` with a README in one commit. Its files are new, as they'd be made."""
    shutil.copytree(_first_commit(), path, dirs_exist_ok=True, copy_function=shutil.copy)
    return path


async def wait_until(condition, timeout: float = 5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not condition():
        if loop.time() > end:
            raise TimeoutError("condition never became true")
        await asyncio.sleep(0.01)


class ScriptedCrew:
    """A runner whose bots are scripted: `script[bot](spec, me)` returns the run's events, where `me` is the caller's
    task, found from its run token exactly as the daemon finds it."""

    def __init__(self, store, tmp_path, *bots, gate=None, **kw):
        self.script = {}
        self.backend = FakeBackend(reply=self._reply, gate=gate)
        self.runner = Runner(store, make_crew(tmp_path, *bots), {"fake": self.backend},
                             worktree_root=tmp_path / "wts", api_url="http://127.0.0.1:8765/v1", **kw)
        self.store = store

    def _reply(self, spec: RunSpec):
        token = spec.mcp_servers[MCP_SERVER_NAME].env["CREWBOTS_TOKEN"]
        me = self.runner.task_for_token(token)
        events = self.script.get(me.bot, lambda spec, me: [Done(final_text=f"{me.bot} did: {spec.prompt[:40]}")])(spec, me)
        return [SessionStarted(session_id=f"s-{me.id}"), *events]

    def specs_for(self, bot_name):
        return [s for s in self.backend.specs if f"You are the {bot_name}." in s.system_prompt]

    def settled(self, task_id):
        return lambda: not self.store.get_task(task_id).active and task_id not in self.runner.running()


class FakeSession(SessionBase):
    """A long-lived session that says back what each turn was asked. It can be made to fail its next turn, or to end
    after a turn as a process that died."""

    def __init__(self, backend, spec: RunSpec):
        self.backend = backend
        self.spec = spec
        self.session_id = spec.resume_session_id or f"s-{len(backend.opened)}"
        self.turns: list[str] = []
        self.closed = False
        self.die_after_turn = False
        self.fail_next = False

    @property
    def alive(self) -> bool:
        return not self.closed

    async def turn(self, prompt: str):
        self.turns.append(prompt)
        if self.backend.gate:
            await self.backend.gate.wait()
        yield SessionStarted(session_id=self.session_id)
        if self.fail_next:
            self.fail_next = False
            yield Done(final_text="", is_error=True, error="it broke")
            return
        yield Text(text=f"did: {prompt}")
        yield Done(final_text=f"did: {prompt}")
        if self.die_after_turn:
            self.closed = True

    async def close(self):
        self.closed = True


class SessionBackend(BackendBase):
    """A backend that keeps sessions open, as Codex's app-server and the Agent SDK do. Set `gate` to hold every turn."""

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


def make(store, tmp_path, backend=None, *bots, **kw):
    """A runner whose bots keep sessions open, and its backend."""
    backend = backend or SessionBackend()
    runner = Runner(store, make_crew(tmp_path, *bots), {"fake": backend}, api_url="http://127.0.0.1:8765/v1", **kw)
    return runner, backend


def settled(store, runner, task_id):
    return lambda: not store.get_task(task_id).active and task_id not in runner.running()


ADMIN = "admin-token"  # the user's token in conftest's `crew_app`
PORT = 8765


class HeldBackend(BackendBase):
    """Every run stays open until `release` is set, so its run token stays valid while the test uses it."""

    def __init__(self):
        self.release = threading.Event()  # the app runs on the test client's own thread and loop
        self.specs = []

    async def run(self, spec):
        self.specs.append(spec)
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        yield Done(final_text="ok")


def start(client, backend, bot_name):
    """Starts a task for `bot_name` and returns (task id, its run token)."""
    task = client.post("/tasks", json={"bot": bot_name, "goal": "go"}, headers={"Authorization": f"Bearer {ADMIN}"}).json()
    for _ in range(200):
        for spec in backend.specs:
            if f"You are the {bot_name}." in spec.system_prompt:
                return task["id"], spec.mcp_servers[MCP_SERVER_NAME].env["CREWBOTS_TOKEN"]
        time.sleep(0.01)
    raise TimeoutError(bot_name)


def as_bot(token):
    return {"Authorization": f"Bearer {token}"}
