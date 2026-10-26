"""What the store hands back: tasks and their statuses, handoffs, reviews and events."""

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    IDLE = "idle"  # the bot finished its turn; the task waits for a follow-up or to be closed
    NEEDS_INPUT = "needs_input"  # the bot escalated a question; a reply resumes it
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # the daemon stopped mid-run; a follow-up message resumes it
    HELD = "held"  # its next turn waits on a budget or the plan's limit; it goes on by itself, or when released


@dataclass(frozen=True)
class Task:
    id: str
    bot: str
    title: str
    status: Status
    created_by: str  # "user", or the name of the bot that delegated it
    parent_id: str | None
    workspace: str | None
    repo: str | None
    worktree_path: str | None
    branch: str | None
    base: str | None
    session_id: str | None
    result: str | None
    error: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    reported: bool
    turns: int
    review_of: str | None
    review_version: int | None
    merge_state: str | None
    merged_commit: str | None
    briefing: str | None
    kind: str
    room_id: str | None
    pr: str | None
    routine: str | None
    budget: int | None
    created_at: str
    updated_at: str

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


ACTIVE = {Status.QUEUED, Status.RUNNING}

@dataclass(frozen=True)
class Handoff:
    """How a bot hands its work back: what it did, where the output is, what is open, what the next step needs."""

    id: int
    task_id: str
    from_bot: str
    done: str
    output: list[str]
    open: str
    needs: str
    at: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True)
class Review:
    id: int
    task_id: str
    work_id: str
    reviewer: str
    version: int
    verdict: Verdict
    summary: str
    findings: list[dict[str, str]]
    at: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StoredEvent:
    id: int
    task_id: str
    kind: str
    data: dict[str, Any]
    at: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)



def _review(row: sqlite3.Row) -> Review:
    return Review(**{**dict(row), "verdict": Verdict(row["verdict"]), "findings": json.loads(row["findings"])})


def _task(row: sqlite3.Row) -> Task:
    values = dict(row)
    values["status"] = Status(values["status"])
    values["reported"] = bool(values["reported"])
    return Task(**values)


def _room(row: sqlite3.Row) -> dict[str, Any]:
    room = dict(row)
    room["members"] = json.loads(room["members"])
    room["stopped"] = room["stopped"] and json.loads(room["stopped"])
    return room


def _room_message(row: sqlite3.Row) -> dict[str, Any]:
    message = dict(row)
    message["attachments"] = json.loads(message.get("attachments") or "[]")
    return message


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
