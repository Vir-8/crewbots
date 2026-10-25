import asyncio
import subprocess
from pathlib import Path

from crewbots.backends import Access, RunSpec
from crewbots.crew import Bot, Crew
from crewbots.events import Done, SessionStarted, Text, Usage


def reply(spec: RunSpec):
    """A run that says back what it was asked."""
    return [
        SessionStarted(session_id=spec.resume_session_id or "s-1"),
        Text(text=f"did: {spec.prompt}"),
        Usage(input_tokens=10, output_tokens=2, cost_usd=0.01),
        Done(final_text=f"did: {spec.prompt}"),
    ]


class FakeBackend:
    """Stands in for a CLI backend in-process. Set `gate` to hold every run until it is set."""

    name = "fake"

    def __init__(self, reply=reply, gate: asyncio.Event | None = None):
        self.reply = reply
        self.gate = gate
        self.specs: list[RunSpec] = []
        self.active = 0
        self.max_active = 0
        self.closed = 0  # runs whose stream was closed, finished or not

    async def run(self, spec: RunSpec):
        self.specs.append(spec)
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


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["git", *a], cwd=path, check=True, capture_output=True)  # noqa: E731
    run("init", "-q", "-b", "main")
    (path / "README.md").write_text("hello\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return path


async def wait_until(condition, timeout: float = 5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not condition():
        if loop.time() > end:
            raise TimeoutError("condition never became true")
        await asyncio.sleep(0.01)
