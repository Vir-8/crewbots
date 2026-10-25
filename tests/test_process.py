import json
import os
import time

from helpers import collect, fake_command, write_fixture

from crewbots.backends.process import stream_jsonl
from crewbots.events import Done, Notice, Text


class EchoParser:
    """Turns {"say": ...} into Text and {"end": ...} into Done."""

    def __init__(self):
        self.last = ""

    def feed(self, obj):
        if "say" in obj:
            self.last = obj["say"]
            return [Text(text=obj["say"])]
        if "end" in obj:
            return [Done(final_text=obj["end"])]
        return []

    def final_text(self):
        return self.last


def run(tmp_path, fixture, timeout_s=None):
    return stream_jsonl(fake_command(fixture), cwd=tmp_path, env=dict(os.environ), parser=EchoParser(),
                        timeout_s=timeout_s)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_dead(pid: int, seconds: float = 5) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


async def test_lines_that_are_not_json_become_notices(tmp_path):
    fixture = tmp_path / "mixed.jsonl"
    fixture.write_text('warming up\n{"say": "hi"}\n{"end": "hi"}\n')

    events = await collect(run(tmp_path, fixture))

    assert events == [Notice(message="warming up"), Text(text="hi"), Done(final_text="hi")]


async def test_only_the_first_done_is_kept(tmp_path):
    fixture = write_fixture(tmp_path, [{"end": "first"}, {"end": "second"}])

    events = await collect(run(tmp_path, fixture))

    assert events == [Done(final_text="first")]


async def test_a_crash_before_finishing_is_a_done_with_stderr(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "3")
    monkeypatch.setenv("FAKE_STDERR", "not logged in")
    fixture = write_fixture(tmp_path, [{"say": "partial"}])

    events = await collect(run(tmp_path, fixture))

    done = events[-1]
    assert isinstance(done, Done) and done.is_error
    assert done.final_text == "partial"
    assert "code 3" in done.error and "not logged in" in done.error


async def test_a_clean_exit_without_finishing_is_still_an_error(tmp_path):
    events = await collect(run(tmp_path, write_fixture(tmp_path, [{"say": "hm"}])))

    assert events[-1].is_error and "code 0" in events[-1].error


async def test_a_timeout_ends_the_run_and_kills_the_cli(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    monkeypatch.setenv("FAKE_SLEEP", "60")

    events = await collect(run(tmp_path, write_fixture(tmp_path, [{"say": "working"}]), timeout_s=0.5))

    assert events[-1] == Done(final_text="working", is_error=True, error="timed out after 0.5s")
    assert wait_dead(json.loads(record.read_text())["pid"])


async def test_stopping_early_kills_the_cli(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    monkeypatch.setenv("FAKE_SLEEP", "60")
    stream = run(tmp_path, write_fixture(tmp_path, [{"say": "one"}, {"say": "two"}]))

    assert await anext(stream) == Text(text="one")
    await stream.aclose()

    assert wait_dead(json.loads(record.read_text())["pid"])
