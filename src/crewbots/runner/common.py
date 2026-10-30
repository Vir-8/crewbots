"""Shared by every part of the runner: its errors, the live bus, and small helpers."""

import asyncio
import contextlib
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from crewbots.backends.base import Session
from crewbots.crew import Bot
from crewbots.events import Usage

REPORT_RESULT_CHARS = 3000
SUBSCRIBER_QUEUE = 10_000
MCP_SERVER_NAME = "crewbots"
MCP_COMMAND = (sys.executable, "-m", "crewbots.mcp_server")


class UnknownBot(KeyError):
    pass


class NotAllowed(PermissionError):
    """A bot asked for something its role or the crew's rules don't allow. The message goes back to the bot."""


class MergeError(RuntimeError):
    """A merge that can't go ahead; nothing was changed."""


@dataclass
class OpenSession:
    session: Session
    token: str  # the session's crew tools act with this for as long as the session is open
    cwd: Path
    last_used: float
    grants: tuple[int, ...] = ()  # what the user had allowed its task when it opened (runner/permissions.py)
    deny_read: tuple[Path, ...] = ()
    coordinator: bool = False  # authority and tool catalog when the session opened
    chief: bool = False


class Bus:
    """Fan-out of live updates to whoever is watching (the app, `crewbots watch`).

    A subscriber that falls too far behind is dropped rather than slowing the crew; it catches up from the store.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None  # where subscribers read, for publishing from a thread

    def publish(self, message: dict[str, Any]) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # a worker thread (a merge running off the loop): hand over to the loop's thread
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self.publish, message)
                return
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._subscribers.discard(queue)

    @contextlib.contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(SUBSCRIBER_QUEUE)
        with contextlib.suppress(RuntimeError):  # sync callers have no loop, and nothing to wake
            self._loop = asyncio.get_running_loop()
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


def effective_network(bot: Bot, grants: list[dict[str, Any]]) -> bool | tuple[str, ...]:
    """A task's network: open once the user allows it any, else its bot's own setting. The browser closes a tab whose
    network changed, so every caller has to agree on this."""
    return any(g["kind"] == "network" for g in grants) or bot.network


def _usage_data(usage: Usage, bot: Bot) -> dict[str, Any]:
    """A turn's usage, with the plan, model and effort that spent it."""
    data = {k: v for k, v in asdict(usage).items() if k != "kind"}
    return data | {"plan": plan(bot.backend), "model": bot.model or "default", "effort": bot.effort}


def plan(backend: str) -> str:
    """The subscription a backend runs on: both Claude backends share Claude's limits, both Codex ones Codex's."""
    return backend.split("-")[0]


def _duration(seconds: float) -> str:
    return f"{seconds:g}s" if seconds < 60 else f"{seconds / 60:g} min" if seconds < 3600 else f"{seconds / 3600:g} h"


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds")


def _clip(text: str | None) -> str | None:
    if text is None or len(text) <= REPORT_RESULT_CHARS:
        return text
    return text[:REPORT_RESULT_CHARS] + f"\n… ({len(text) - REPORT_RESULT_CHARS} more characters; ask the task for them)"
