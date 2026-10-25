"""Everything the crew has done, in one SQLite file: tasks, and every event of every run.

A task is the unit of work and of conversation. It has exactly one owner bot, its own backend session (so a follow-up
message resumes where it left off), optionally its own worktree, and a transcript made of its events.
"""

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from crewbots.events import Event

TITLE_CHARS = 80

SCHEMA = """
create table if not exists tasks (
    id            text primary key,
    bot           text not null,
    title         text not null,
    status        text not null,
    created_by    text not null,
    parent_id     text references tasks(id),
    workspace     text,
    repo          text,
    worktree_path text,
    branch        text,
    base          text,
    session_id    text,
    result        text,
    error         text,
    input_tokens  integer not null default 0,
    output_tokens integer not null default 0,
    cost_usd      real not null default 0,
    reported      integer not null default 0,  -- whether its outcome has gone back to the task that delegated it
    created_at    text not null,
    updated_at    text not null
);
create table if not exists events (
    id      integer primary key autoincrement,
    task_id text not null references tasks(id),
    kind    text not null,
    data    text not null,
    at      text not null
);
create index if not exists events_by_task on events(task_id, id);
create index if not exists tasks_by_parent on tasks(parent_id);
create table if not exists handoffs (
    id       integer primary key autoincrement,
    task_id  text not null references tasks(id),
    from_bot text not null,
    done     text not null,
    output   text not null,  -- JSON list of paths, branches, links
    open     text not null,
    needs    text not null,
    at       text not null
);
"""
MIGRATIONS = {"reported": "alter table tasks add column reported integer not null default 0"}


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    IDLE = "idle"  # the bot finished its turn; the task waits for a follow-up or to be closed
    NEEDS_INPUT = "needs_input"  # the bot escalated a question; a reply resumes it
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # the daemon stopped mid-run; a follow-up message resumes it


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


@dataclass(frozen=True)
class StoredEvent:
    id: int
    task_id: str
    kind: str
    data: dict[str, Any]
    at: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("pragma foreign_keys=on")
        self.db.executescript(SCHEMA)
        columns = {r["name"] for r in self.db.execute("pragma table_info(tasks)")}
        for column, sql in MIGRATIONS.items():
            if column not in columns:
                self.db.execute(sql)

    def close(self) -> None:
        self.db.close()

    def create_task(
        self, bot: str, goal: str, *, created_by: str = "user", parent_id: str | None = None, workspace: str | None = None,
    ) -> Task:
        task_id = f"t_{uuid.uuid4().hex[:10]}"
        now = _now()
        first_line = goal.strip().splitlines()[0] if goal.strip() else "(empty)"
        title = first_line if len(first_line) <= TITLE_CHARS else first_line[: TITLE_CHARS - 1] + "…"
        self.db.execute(
            "insert into tasks (id, bot, title, status, created_by, parent_id, workspace, created_at, updated_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, bot, title, Status.QUEUED, created_by, parent_id, workspace, now, now),
        )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Task:
        row = self.db.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _task(row)

    def list_tasks(self, status: Status | None = None, bot: str | None = None, limit: int = 100) -> list[Task]:
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if bot:
            where.append("bot = ?")
            args.append(bot)
        sql = "select * from tasks" + (" where " + " and ".join(where) if where else "")
        rows = self.db.execute(sql + " order by created_at desc, id desc limit ?", (*args, limit)).fetchall()
        return [_task(r) for r in rows]

    def children(self, parent_id: str) -> list[Task]:
        rows = self.db.execute("select * from tasks where parent_id = ? order by created_at, id", (parent_id,)).fetchall()
        return [_task(r) for r in rows]

    def add_handoff(self, task_id: str, from_bot: str, done: str, output: list[str], open: str, needs: str) -> Handoff:
        now = _now()
        cur = self.db.execute(
            "insert into handoffs (task_id, from_bot, done, output, open, needs, at) values (?, ?, ?, ?, ?, ?, ?)",
            (task_id, from_bot, done, json.dumps(output), open, needs, now),
        )
        return Handoff(id=cur.lastrowid, task_id=task_id, from_bot=from_bot, done=done, output=output, open=open,
                       needs=needs, at=now)

    def latest_handoff(self, task_id: str) -> Handoff | None:
        row = self.db.execute("select * from handoffs where task_id = ? order by id desc limit 1", (task_id,)).fetchone()
        if row is None:
            return None
        return Handoff(**{**dict(row), "output": json.loads(row["output"])})

    def update_task(self, task_id: str, **fields: Any) -> Task:
        allowed = set(Task.__dataclass_fields__) - {"id", "bot", "created_by", "created_at", "updated_at"}
        if bad := set(fields) - allowed:
            raise ValueError(f"cannot update {sorted(bad)}")  # the owner, in particular, never changes here
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        cur = self.db.execute(f"update tasks set {assignments} where id = ?", (*fields.values(), task_id))
        if cur.rowcount == 0:
            raise KeyError(task_id)
        return self.get_task(task_id)

    def add_usage(self, task_id: str, input_tokens: int, output_tokens: int, cost_usd: float | None) -> None:
        self.db.execute(
            "update tasks set input_tokens = input_tokens + ?, output_tokens = output_tokens + ?,"
            " cost_usd = cost_usd + ? where id = ?",
            (input_tokens, output_tokens, cost_usd or 0.0, task_id),
        )

    def mark_interrupted(self) -> list[str]:
        """On startup: anything still marked running or queued was cut off when the daemon last stopped."""
        rows = self.db.execute("select id from tasks where status in (?, ?)", (Status.RUNNING, Status.QUEUED)).fetchall()
        for row in rows:
            self.update_task(row["id"], status=Status.INTERRUPTED, error="the daemon stopped during this task")
        return [r["id"] for r in rows]

    def append_event(self, task_id: str, event: Event | None = None, *, kind: str | None = None,
                     data: dict[str, Any] | None = None) -> StoredEvent:
        """Stores a backend event, or a crewbots one (`kind` and `data`, e.g. a user message)."""
        if event is not None:
            kind, data = event.kind, {k: v for k, v in asdict(event).items() if k != "kind"}
        assert kind is not None
        now = _now()
        cur = self.db.execute(
            "insert into events (task_id, kind, data, at) values (?, ?, ?, ?)",
            (task_id, kind, json.dumps(data or {}), now),
        )
        return StoredEvent(id=cur.lastrowid, task_id=task_id, kind=kind, data=data or {}, at=now)

    def events(self, task_id: str | None = None, after: int = 0, limit: int = 1000) -> list[StoredEvent]:
        if task_id:
            sql, args = "select * from events where task_id = ? and id > ? order by id limit ?", (task_id, after, limit)
        else:
            sql, args = "select * from events where id > ? order by id limit ?", (after, limit)
        return [
            StoredEvent(id=r["id"], task_id=r["task_id"], kind=r["kind"], data=json.loads(r["data"]), at=r["at"])
            for r in self.db.execute(sql, args).fetchall()
        ]


def _task(row: sqlite3.Row) -> Task:
    values = dict(row)
    values["status"] = Status(values["status"])
    values["reported"] = bool(values["reported"])
    return Task(**values)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
