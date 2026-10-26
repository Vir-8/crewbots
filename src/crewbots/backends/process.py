"""Runs a CLI that prints one JSON object per line, and turns its output into events.

The process runs in its own process group, so stopping a run also stops whatever the CLI started (shells, MCP servers).
"""

import asyncio
import json
import os
import signal
from collections import deque
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Protocol

from crewbots.events import Done, Event, Notice

LINE_LIMIT = 64 * 1024 * 1024  # one event can carry a whole file's contents
KILL_GRACE_S = 3.0
STDERR_TAIL_LINES = 40


class LineParser(Protocol):
    def feed(self, obj: dict[str, Any]) -> list[Event]:
        """Events for one line of CLI output. Emitting a `Done` is allowed; only the first one is kept."""
        ...

    def final_text(self) -> str:
        """The last thing the agent said, for when the CLI ends without its own closing event."""
        ...


async def stream_jsonl(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    parser: LineParser,
    stdin_text: str = "",
    timeout_s: float | None = None,
) -> AsyncIterator[Event]:
    """`stdin_text` is written and closed at once; the CLIs take the prompt this way, so no argument parsing can eat it."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        limit=LINE_LIMIT,
    )
    assert proc.stdin and proc.stdout and proc.stderr
    try:
        proc.stdin.write(stdin_text.encode())
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        pass  # the CLI exited without reading its input; its exit code and stderr say why
    finally:
        proc.stdin.close()
    stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
    stderr_task = asyncio.create_task(_drain(proc.stderr, stderr_tail))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s if timeout_s else None
    done_seen = False
    try:
        while True:
            try:
                remaining = None if deadline is None else max(0.0, deadline - loop.time())
                line = await asyncio.wait_for(proc.stdout.readline(), remaining)
            except TimeoutError:
                yield Done(final_text=parser.final_text(), is_error=True, error=f"timed out after {timeout_s:g}s")
                return
            if not line:
                break
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                yield Notice(message=text)
                continue
            if not isinstance(obj, dict):
                yield Notice(message=text)
                continue
            for event in parser.feed(obj):
                if isinstance(event, Done):
                    if done_seen:
                        continue
                    done_seen = True
                yield event

        returncode = await proc.wait()
        await stderr_task
        if not done_seen:
            detail = "\n".join(stderr_tail).strip() or "no output on stderr"
            yield Done(
                final_text=parser.final_text(),
                is_error=True,
                error=f"{Path(argv[0]).name} exited with code {returncode} before finishing: {detail}",
            )
    finally:
        await _stop(proc)
        stderr_task.cancel()


async def _drain(stream: asyncio.StreamReader, tail: deque[str]) -> None:
    while line := await stream.readline():
        tail.append(line.decode(errors="replace").rstrip())


async def _stop(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    for sig, wait_s in ((signal.SIGTERM, KILL_GRACE_S), (signal.SIGKILL, None)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), wait_s)
            return
        except TimeoutError:
            continue


class JsonlProcess:
    """A long-lived CLI that takes and gives one JSON object per line: what a session talks to.

    It runs in its own process group, like a one-shot run, so stopping it stops everything it started.
    """

    def __init__(self, proc: asyncio.subprocess.Process, name: str):
        self.proc = proc
        self.name = name
        self.stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        assert proc.stderr
        self._stderr_task = asyncio.create_task(_drain(proc.stderr, self.stderr_tail))

    @classmethod
    async def start(cls, argv: Sequence[str], *, cwd: Path, env: dict[str, str]) -> "JsonlProcess":
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=LINE_LIMIT,
        )
        return cls(proc, Path(argv[0]).name)

    @property
    def alive(self) -> bool:
        return self.proc.returncode is None

    async def send(self, obj: dict[str, Any]) -> None:
        assert self.proc.stdin
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def read(self, timeout_s: float | None = None) -> dict[str, Any] | str | None:
        """The next line: a JSON object, the raw text if it isn't one, or None once the process has ended."""
        assert self.proc.stdout
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout_s)
            if not line:
                return None
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                return text
            return obj if isinstance(obj, dict) else text

    def exit_detail(self) -> str:
        detail = "\n".join(self.stderr_tail).strip() or "no output on stderr"
        return f"{self.name} exited with code {self.proc.returncode}: {detail}"

    async def stop(self) -> None:
        if self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.close()
        await _stop(self.proc)
        self._stderr_task.cancel()
