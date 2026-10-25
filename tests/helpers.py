import json
import sys
from pathlib import Path

from crewbots.events import Event

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CLI = Path(__file__).parent / "fake_cli.py"


def fake_command(fixture: str | Path) -> list[str]:
    """A backend `command` that runs the fake CLI over a fixture ("-" for no output)."""
    return [sys.executable, str(FAKE_CLI), str(FIXTURES / fixture) if fixture != "-" else "-"]


def write_fixture(tmp_path: Path, lines: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def lines(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


async def collect(stream) -> list[Event]:
    return [event async for event in stream]


def kinds(events: list[Event]) -> list[str]:
    return [e.kind for e in events]
