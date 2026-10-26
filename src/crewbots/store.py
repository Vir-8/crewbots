"""Everything the crew has done, in one SQLite file: tasks, and every event of every run.

A task is the unit of work and of conversation. It has exactly one owner bot, its own backend session (so a follow-up
message resumes where it left off), optionally its own worktree, and a transcript made of its events. There are three
kinds:

- a **chat**: a bot's one ongoing conversation with the user, which never ends;
- a **seat**: a bot's own conversation inside a group chat (a room), fed the room's new messages;
- a **thread**: a piece of work, opened from a chat, a seat, a routine or another thread.

A room is a group chat: the user and some bots, with one of them the lead. Its messages are kept on their own.
"""

import json
import re
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
    turns         integer not null default 0,  -- runs that finished; a review is of the work as of one of these
    review_of     text references tasks(id),   -- set on a review task: the work it reviews
    review_version integer,                    -- the work's `turns` when this review was asked for
    merge_state   text,                        -- null, proposed (by its coordinator), or merged (by the user)
    merged_commit text,
    briefing      text,                        -- where things stood, carried into a fresh session at rollover
    kind          text not null default 'thread', -- chat, seat or thread
    room_id       text,                        -- a seat's room
    pr            text,                        -- a review's pull request, owner/name#number
    routine       text,                        -- the routine that started it, on a routine's top-level task
    budget        integer,                     -- tokens it may use, with everything it delegates (a routine's budget)
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
create table if not exists reviews (
    id       integer primary key autoincrement,
    task_id  text not null references tasks(id),  -- the review task
    work_id  text not null references tasks(id),  -- the work it reviewed
    reviewer text not null,
    version  integer not null,
    verdict  text not null,                       -- pass or fail
    summary  text not null,
    findings text not null,                       -- JSON list of {location, problem, scenario}
    at       text not null
);
create index if not exists reviews_by_work on reviews(work_id, version);
create index if not exists events_by_kind on events(kind, at);
create table if not exists rooms (
    id         text primary key,
    title      text not null,
    lead       text not null,  -- the bot that answers a message that mentions no one
    members    text not null,  -- JSON list of bot names
    created_at text not null,
    updated_at text not null
);
create table if not exists room_messages (
    id      integer primary key autoincrement,
    room_id text not null references rooms(id),
    author  text not null,          -- user, or a bot's name
    text    text not null,
    depth   integer not null default 0,  -- how many bot replies away from the user's message it is
    at      text not null
);
create index if not exists room_messages_by_room on room_messages(room_id, id);
create table if not exists reads (   -- how far a conversation has been seen: by the user, or by a seat of a room
    key  text primary key,
    seen integer not null
);
create table if not exists routines (
    name         text primary key,
    checked_at   text not null,  -- when it was last checked: a schedule runs for a time after this
    last_run_at  text,
    last_task_id text
);
create table if not exists triggered (  -- what each trigger has already started on, so it starts once
    routine text not null,
    key     text not null,
    at      text not null,
    primary key (routine, key)
);
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
# Full-text search over what the crew said and decided: the `recall` tool. Kept in step with events as they're added.
SEARCH = """
create virtual table search using fts5(
    text, task_id unindexed, event_id unindexed, kind unindexed, at unindexed, tokenize = 'porter unicode61'
);
"""
# A task and everything it delegated, however deep: the ids, for `id in (...)`.
TREE = ("with recursive tree(id) as (select ? union select t.id from tasks t join tree on t.parent_id = tree.id)"
        " select id from tree")
SEARCHABLE = {"message", "text", "handoff", "review", "escalation", "merge_proposed"}
SNIPPET_TOKENS = 18

MIGRATIONS = {  # columns added after a crew's database may already exist
    "reported": "alter table tasks add column reported integer not null default 0",
    "turns": "alter table tasks add column turns integer not null default 0",
    "review_of": "alter table tasks add column review_of text references tasks(id)",
    "review_version": "alter table tasks add column review_version integer",
    "merge_state": "alter table tasks add column merge_state text",
    "merged_commit": "alter table tasks add column merged_commit text",
    "briefing": "alter table tasks add column briefing text",
    "routine": "alter table tasks add column routine text",
    "budget": "alter table tasks add column budget integer",
    "kind": "alter table tasks add column kind text not null default 'thread'",
    "room_id": "alter table tasks add column room_id text",
    "pr": "alter table tasks add column pr text",
}


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
        if not self.db.execute("select 1 from sqlite_master where name = 'search'").fetchone():
            self.db.executescript(SEARCH)
            self._index_history()

    def close(self) -> None:
        self.db.close()

    def create_task(
        self, bot: str, goal: str, *, created_by: str = "user", parent_id: str | None = None, workspace: str | None = None,
        review_of: str | None = None, review_version: int | None = None, routine: str | None = None,
        budget: int | None = None, title: str | None = None, kind: str = "thread", room_id: str | None = None,
        status: "Status | None" = None, pr: str | None = None,
    ) -> Task:
        task_id = f"t_{uuid.uuid4().hex[:10]}"
        now = _now()
        first_line = title or (goal.strip().splitlines()[0] if goal.strip() else "(empty)")
        title = first_line if len(first_line) <= TITLE_CHARS else first_line[: TITLE_CHARS - 1] + "…"
        self.db.execute(
            "insert into tasks (id, bot, title, status, created_by, parent_id, workspace, review_of, review_version,"
            " routine, budget, kind, room_id, pr, created_at, updated_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, bot, title, status or Status.QUEUED, created_by, parent_id, workspace, review_of, review_version,
             routine, budget, kind, room_id, pr, now, now),
        )
        self.db.execute("insert into search (text, task_id, event_id, kind, at) values (?, ?, 0, 'task', ?)",
                        (title, task_id, now))
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

    def reviewers_of(self, work_id: str) -> list[Task]:
        """The review tasks of a piece of work, oldest first."""
        rows = self.db.execute("select * from tasks where review_of = ? order by created_at, id", (work_id,)).fetchall()
        return [_task(r) for r in rows]

    def add_review(self, task_id: str, work_id: str, reviewer: str, version: int, verdict: Verdict, summary: str,
                   findings: list[dict[str, str]]) -> Review:
        now = _now()
        cur = self.db.execute(
            "insert into reviews (task_id, work_id, reviewer, version, verdict, summary, findings, at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, work_id, reviewer, version, verdict, summary, json.dumps(findings), now),
        )
        return Review(id=cur.lastrowid, task_id=task_id, work_id=work_id, reviewer=reviewer, version=version,
                      verdict=verdict, summary=summary, findings=findings, at=now)

    def latest_review(self, task_id: str) -> Review | None:
        """The verdict a review task gave, if it gave one; a later verdict replaces an earlier one."""
        row = self.db.execute("select * from reviews where task_id = ? order by id desc limit 1", (task_id,)).fetchone()
        return _review(row) if row else None

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
        rows = self.db.execute("select id from tasks where status in (?, ?, ?)",
                               (Status.RUNNING, Status.QUEUED, Status.HELD)).fetchall()
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
        self._index(task_id, cur.lastrowid, kind, data or {}, now)
        return StoredEvent(id=cur.lastrowid, task_id=task_id, kind=kind, data=data or {}, at=now)

    def search(self, query: str, limit: int = 20, exclude_task: str | None = None) -> list[dict[str, Any]]:
        """What the crew said or decided that matches `query`, best first: all the words if anything has them all,
        otherwise any of them."""
        words = re.findall(r"\w+", query.lower())
        if not words:
            return []
        for joiner in (" ", " OR "):
            match = joiner.join(f'"{w}"' for w in words)
            rows = self.db.execute(
                f"select s.task_id, s.kind, s.at, snippet(search, 0, '«', '»', '…', {SNIPPET_TOKENS}) as snippet,"
                " t.bot, t.title from search s join tasks t on t.id = s.task_id"
                " where search match ? and s.task_id != ? order by bm25(search) limit ?",
                (match, exclude_task or "", limit),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
        return []

    def _index(self, task_id: str, event_id: int, kind: str, data: dict[str, Any], at: str) -> None:
        if kind not in SEARCHABLE:
            return
        text = _searchable_text(kind, data)
        if text:
            self.db.execute("insert into search (text, task_id, event_id, kind, at) values (?, ?, ?, ?, ?)",
                            (text, task_id, event_id, kind, at))

    def _index_history(self) -> None:
        for row in self.db.execute("select id, title, created_at from tasks").fetchall():
            self.db.execute("insert into search (text, task_id, event_id, kind, at) values (?, ?, 0, 'task', ?)",
                            (row["title"], row["id"], row["created_at"]))
        for row in self.db.execute("select * from events").fetchall():
            self._index(row["task_id"], row["id"], row["kind"], json.loads(row["data"]), row["at"])

    # Budgets. Tokens here are the fresh ones: input the model hadn't cached, plus output. Cached input is most of a
    # long conversation's input, and costs a plan far less.

    def bot_usage(self, bot: str, since: str) -> int:
        return self._fresh_tokens("join tasks t on t.id = e.task_id", "t.bot = ? and e.at >= ?", (bot, since))

    def tree_usage(self, root_id: str) -> int:
        """Tokens used by a task and everything it delegated, however deep."""
        return self._fresh_tokens("", f"e.task_id in ({TREE})", (root_id,))

    def work_of(self, task_id: str) -> Task:
        """The piece of work a task belongs to: the outermost thread above it (or itself), stopping at a chat or seat.
        A budget or a release covers that much, not a bot's whole never-ending chat."""
        task = self.get_task(task_id)
        while task.parent_id:
            parent = self.get_task(task.parent_id)
            if parent.kind != "thread":
                break
            task = parent
        return task

    def root_of(self, task_id: str) -> Task:
        task = self.get_task(task_id)
        while task.parent_id:
            task = self.get_task(task.parent_id)
        return task

    def _fresh_tokens(self, join: str, where: str, args: tuple[Any, ...]) -> int:
        fresh = ("json_extract(e.data, '$.input_tokens') - coalesce(json_extract(e.data, '$.cached_input_tokens'), 0)"
                 " + json_extract(e.data, '$.output_tokens')")
        sql = f"select coalesce(sum({fresh}), 0) from events e {join} where e.kind = 'usage' and {where}"
        return int(self.db.execute(sql, args).fetchone()[0])

    # Routines.

    def routine_state(self, name: str) -> dict[str, Any] | None:
        row = self.db.execute("select * from routines where name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def routine_checked(self, name: str, at: str) -> None:
        self.db.execute("insert into routines (name, checked_at) values (?, ?) on conflict(name) do update set"
                        " checked_at = excluded.checked_at", (name, at))

    def routine_ran(self, name: str, at: str, task_id: str) -> None:
        self.db.execute("insert into routines (name, checked_at, last_run_at, last_task_id) values (?, ?, ?, ?)"
                        " on conflict(name) do update set last_run_at = excluded.last_run_at,"
                        " last_task_id = excluded.last_task_id", (name, at, at, task_id))

    def triggered(self, routine: str, keys: list[str]) -> set[str]:
        """Which of `keys` this trigger already started on."""
        if not keys:
            return set()
        rows = self.db.execute(f"select key from triggered where routine = ? and key in ({','.join('?' * len(keys))})",
                               (routine, *keys)).fetchall()
        return {r["key"] for r in rows}

    def mark_triggered(self, routine: str, keys: list[str]) -> None:
        now = _now()
        self.db.executemany("insert or ignore into triggered (routine, key, at) values (?, ?, ?)",
                            [(routine, k, now) for k in keys])

    def activity(self, since: str, exclude_routine: str | None = None) -> list[Task]:
        """Threads of work with anything new since `since`, oldest first: each one that isn't inside another thread,
        leaving out one routine's own runs."""
        rows = self.db.execute(
            "select t.* from tasks t left join tasks p on p.id = t.parent_id where t.kind = 'thread' and t.pr is null"
            " and (p.id is null or p.kind != 'thread') and t.updated_at >= ? and coalesce(t.routine, '') != ?"
            " order by t.created_at, t.id", (since, exclude_routine or "")).fetchall()
        return [_task(r) for r in rows]

    # Chats and rooms.

    def chat_of(self, bot: str) -> Task | None:
        row = self.db.execute("select * from tasks where kind = 'chat' and bot = ? order by created_at limit 1",
                              (bot,)).fetchone()
        return _task(row) if row else None

    def seat_of(self, room_id: str, bot: str) -> Task | None:
        row = self.db.execute("select * from tasks where kind = 'seat' and room_id = ? and bot = ?",
                              (room_id, bot)).fetchone()
        return _task(row) if row else None

    def threads_of(self, bot: str, limit: int = 50) -> list[Task]:
        """A bot's work, newest first. Code reviews are kept apart, with their pull requests."""
        rows = self.db.execute("select * from tasks where kind = 'thread' and pr is null and bot = ?"
                               " order by updated_at desc limit ?", (bot, limit)).fetchall()
        return [_task(r) for r in rows]

    def reviews_of(self, pr: str) -> list[Task]:
        rows = self.db.execute("select * from tasks where pr = ? order by created_at", (pr,)).fetchall()
        return [_task(r) for r in rows]

    def reviewed_prs(self) -> dict[str, str]:
        """Each pull request that has a review, with when it was last touched."""
        rows = self.db.execute("select pr, max(updated_at) as at from tasks where pr is not null group by pr").fetchall()
        return {r["pr"]: r["at"] for r in rows}

    def usage_by(self, since: str) -> list[dict[str, Any]]:
        """Fresh tokens and turns since `since`, summed by local day, bot, task kind, plan, model and effort: what the
        usage page is made from. Summed here rather than in Python, so a year of turns comes back as a few rows."""
        rows = self.db.execute(
            "select date(e.at, 'localtime') as day, t.bot, t.kind, json_extract(e.data, '$.plan') as plan,"
            " json_extract(e.data, '$.model') as model, json_extract(e.data, '$.effort') as effort,"
            " sum(max(0, coalesce(json_extract(e.data, '$.input_tokens'), 0)"
            " - coalesce(json_extract(e.data, '$.cached_input_tokens'), 0))"
            " + coalesce(json_extract(e.data, '$.output_tokens'), 0)) as fresh, count(*) as turns,"
            " sum(coalesce(json_extract(e.data, '$.input_tokens'), 0)) as input,"
            " sum(coalesce(json_extract(e.data, '$.cached_input_tokens'), 0)) as cached"
            " from events e join tasks t on t.id = e.task_id where e.kind = 'usage' and e.at >= ?"
            " group by day, t.bot, t.kind, plan, model, effort", (since,)).fetchall()
        return [dict(r) for r in rows]

    def longest_turn(self, since: str) -> tuple[str, float] | None:
        """(task id, seconds) of the longest turn that finished since `since`, from its ask to its end."""
        rows = self.db.execute("select task_id, kind, at from events where kind in ('message', 'done') and at >= ?"
                               " order by id", (since,)).fetchall()
        asked: dict[str, str] = {}
        longest: tuple[str, float] | None = None
        for r in rows:
            if r["kind"] == "message":
                asked.setdefault(r["task_id"], r["at"])
            elif r["task_id"] in asked:
                seconds = (datetime.fromisoformat(r["at"]) - datetime.fromisoformat(asked.pop(r["task_id"]))).total_seconds()
                if longest is None or seconds > longest[1]:
                    longest = (r["task_id"], seconds)
        return longest

    def count_since(self, since: str) -> dict[str, int]:
        one = lambda sql, *a: self.db.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "threads": one("select count(*) from tasks where kind = 'thread' and created_at >= ?", since),
            "reviews": one("select count(*) from tasks where kind = 'review' and created_at >= ?", since),
            "routine_runs": one("select count(*) from events where kind = 'message' and at >= ?"
                                " and json_extract(data, '$.from') like 'routine:%'", since),
            "room_messages": one("select count(*) from room_messages where at >= ?", since),
        }

    def create_room(self, title: str, lead: str, members: list[str]) -> dict[str, Any]:
        room_id = f"r_{uuid.uuid4().hex[:10]}"
        now = _now()
        self.db.execute("insert into rooms (id, title, lead, members, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
                        (room_id, title, lead, json.dumps(members), now, now))
        return self.room(room_id)

    def room(self, room_id: str) -> dict[str, Any]:
        row = self.db.execute("select * from rooms where id = ?", (room_id,)).fetchone()
        if row is None:
            raise KeyError(room_id)
        return {**dict(row), "members": json.loads(row["members"])}

    def rooms(self) -> list[dict[str, Any]]:
        rows = self.db.execute("select * from rooms order by created_at").fetchall()
        return [{**dict(r), "members": json.loads(r["members"])} for r in rows]

    def add_room_message(self, room_id: str, author: str, text: str, depth: int = 0) -> dict[str, Any]:
        now = _now()
        cur = self.db.execute("insert into room_messages (room_id, author, text, depth, at) values (?, ?, ?, ?, ?)",
                              (room_id, author, text, depth, now))
        self.db.execute("update rooms set updated_at = ? where id = ?", (now, room_id))
        return {"id": cur.lastrowid, "room_id": room_id, "author": author, "text": text, "depth": depth, "at": now}

    def room_messages(self, room_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.db.execute("select * from room_messages where room_id = ? and id > ? order by id limit ?",
                               (room_id, after, limit)).fetchall()
        return [dict(r) for r in rows]

    def last_room_message(self, room_id: str) -> dict[str, Any] | None:
        row = self.db.execute("select * from room_messages where room_id = ? order by id desc limit 1",
                              (room_id,)).fetchone()
        return dict(row) if row else None

    def seen(self, key: str) -> int:
        row = self.db.execute("select seen from reads where key = ?", (key,)).fetchone()
        return row["seen"] if row else 0

    def mark_seen(self, key: str, seen: int) -> None:
        self.db.execute("insert into reads (key, seen) values (?, ?) on conflict(key) do update set"
                        " seen = max(seen, excluded.seen)", (key, seen))

    def last_event(self, task_id: str, kinds: tuple[str, ...]) -> StoredEvent | None:
        row = self.db.execute(
            f"select * from events where task_id = ? and kind in ({','.join('?' * len(kinds))}) order by id desc limit 1",
            (task_id, *kinds)).fetchone()
        if row is None:
            return None
        return StoredEvent(id=row["id"], task_id=row["task_id"], kind=row["kind"], data=json.loads(row["data"]),
                           at=row["at"])

    def count_events_after(self, task_id: str, after: int, kinds: tuple[str, ...]) -> int:
        return self.db.execute(
            f"select count(*) from events where task_id = ? and id > ? and kind in ({','.join('?' * len(kinds))})",
            (task_id, after, *kinds)).fetchone()[0]

    def tree(self, root_id: str) -> list[Task]:
        rows = self.db.execute(f"select * from tasks where id in ({TREE}) order by created_at, id", (root_id,)).fetchall()
        return [_task(r) for r in rows]

    def events_since(self, since: str, kinds: tuple[str, ...], task_ids: list[str]) -> list[StoredEvent]:
        if not task_ids:
            return []
        rows = self.db.execute(
            f"select * from events where at >= ? and kind in ({','.join('?' * len(kinds))})"
            f" and task_id in ({','.join('?' * len(task_ids))}) order by id", (since, *kinds, *task_ids)).fetchall()
        return [StoredEvent(id=r["id"], task_id=r["task_id"], kind=r["kind"], data=json.loads(r["data"]), at=r["at"])
                for r in rows]

    def recent_events(self, limit: int = 200, kinds: tuple[str, ...] | None = None) -> list[StoredEvent]:
        """The newest events across the crew, oldest first."""
        where = f" where kind in ({','.join('?' * len(kinds))})" if kinds else ""
        rows = self.db.execute(f"select * from events{where} order by id desc limit ?", (*(kinds or ()), limit)).fetchall()
        return [
            StoredEvent(id=r["id"], task_id=r["task_id"], kind=r["kind"], data=json.loads(r["data"]), at=r["at"])
            for r in reversed(rows)
        ]

    def latest_event_id(self) -> int:
        return self.db.execute("select coalesce(max(id), 0) from events").fetchone()[0]

    def events(self, task_id: str | None = None, after: int = 0, limit: int = 1000) -> list[StoredEvent]:
        if task_id:
            sql, args = "select * from events where task_id = ? and id > ? order by id limit ?", (task_id, after, limit)
        else:
            sql, args = "select * from events where id > ? order by id limit ?", (after, limit)
        return [
            StoredEvent(id=r["id"], task_id=r["task_id"], kind=r["kind"], data=json.loads(r["data"]), at=r["at"])
            for r in self.db.execute(sql, args).fetchall()
        ]


def _searchable_text(kind: str, data: dict[str, Any]) -> str:
    match kind:
        case "message" | "text":
            return str(data.get("text", ""))
        case "handoff":
            return " ".join(str(data.get(k, "")) for k in ("done", "open", "needs")) + " " + " ".join(data.get("output") or [])
        case "review":
            findings = " ".join(f"{f.get('location', '')} {f.get('problem', '')}" for f in data.get("findings") or [])
            return f"{data.get('verdict', '')} {data.get('summary', '')} {findings}"
        case "escalation":
            return str(data.get("question", ""))
        case "merge_proposed":
            return str(data.get("summary", ""))
    return ""


def _review(row: sqlite3.Row) -> Review:
    return Review(**{**dict(row), "verdict": Verdict(row["verdict"]), "findings": json.loads(row["findings"])})


def _task(row: sqlite3.Row) -> Task:
    values = dict(row)
    values["status"] = Status(values["status"])
    values["reported"] = bool(values["reported"])
    return Task(**values)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
