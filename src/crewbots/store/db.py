"""The store's database: one SQLite file, its connection, its layout, and the steps that bring an older one up to
date.

A new database gets today's layout at once (`SCHEMA`, `SEARCH`). An existing one runs each numbered step in
`MIGRATIONS` it hasn't had yet, in order, and `pragma user_version` records how many it has had. So a change to the
layout is a new step at the end of the list, and the same change in `SCHEMA` for new databases; a test checks that
an old database, migrated, ends up laid out like a new one.
"""

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    updated_at text not null,
    max_depth  integer,        -- bot replies in a row before the bots stop; null for no limit
    stopped    text,           -- JSON {after, waiting, owed} once the bots stopped there, until someone carries on
    muted      integer not null default 0,  -- no notifications from it
    archived_at text,                       -- while it's archived: kept, out of the way, and its bots quiet
    crew       integer not null default 0   -- the crew's own group chat, with every bot
);
create table if not exists room_messages (
    id      integer primary key autoincrement,
    room_id text not null references rooms(id),
    author  text not null,          -- user, or a bot's name
    text    text not null,
    depth   integer not null default 0,  -- how many bot replies away from the user's message it is
    at      text not null,
    attachments text                     -- JSON list, like a message event's
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
create table if not exists review_drafts (  -- line comments for a pull request review, private until the user submits
    id          integer primary key autoincrement,
    repo        text not null,
    number      integer not null,
    path        text not null,
    line        integer not null,
    side        text not null default 'RIGHT',  -- RIGHT: the pull request's version of the file; LEFT: the base's
    body        text not null,
    author      text not null,  -- "user", or the reviewer that drafted it (reviewer:<backend>:<model>:<effort>)
    task_id     text,           -- the review it came from
    commit_sha  text,           -- the head it was written against
    created_at  text not null,
    updated_at  text not null
);
create index if not exists review_drafts_by_pr on review_drafts(repo, number, id);
create table if not exists pending_turns (  -- a turn under way or waiting, so a restart can carry it on
    task_id text primary key references tasks(id),
    prompt  text not null,               -- what the turn was asked
    started integer not null default 0,  -- whether the bot had it: then it carries on rather than starting over
    waiting text not null default '[]',  -- JSON list of messages sent while it ran, for the turn after
    resumed integer not null default 0   -- already carried on after a restart once: not again
);
"""

# What bots did to each other (migration 4), kept when the group chat or task it was about goes.
AGENT_LOG = """
create table if not exists agent_log (
    id      integer primary key autoincrement,
    at      text not null,
    kind    text not null,   -- delegate, review, ask, message, post, open_group, add_to_group, refused
    bot     text not null,   -- the bot that did it
    target  text,            -- the bot it was to, if one
    task_id text,            -- the task it made or went to
    room_id text,            -- the group chat, for a post or a group
    text    text not null default ''
);
"""
SCHEMA += AGENT_LOG

# What each task's session started with (migration 5): a digest of each changing part of its bot's instructions, so
# a resumed session hears what changed since (the runner's `<crew-updates>`).
PRIMED = """
create table if not exists primed (
    task_id text primary key references tasks(id),
    parts   text not null   -- JSON {part: digest}
);
"""
SCHEMA += PRIMED

# The queries bots ask `recall` and what came back (migration 6), to build an evaluation from real use.
RECALL_LOG = """
create table if not exists recall_log (
    id      integer primary key autoincrement,
    at      text not null,
    task_id text not null,
    bot     text not null,
    query   text not null,
    hits    text not null   -- JSON list of what came back: task ids, or log and note paths
);
"""
SCHEMA += RECALL_LOG

# What happened around the crew (`sources.py`), and the standing intents waiting for it (migration 7).
HAPPENINGS = """
create table if not exists crew_events (
    id      integer primary key autoincrement,
    at      text not null,
    kind    text not null,   -- commit, branch, file, review_requested, ci_failed, ci_passed, thread_done
    subject text not null,   -- a repository, a folder, a pull request or a task
    text    text not null
);
create index if not exists crew_events_by_at on crew_events(at);
create table if not exists intents (
    id            integer primary key autoincrement,
    bot           text not null,
    task_id       text,                       -- where it was set
    kind          text not null,              -- the kind of event it waits for
    match         text not null default '',   -- words the event must mention, if any
    text          text not null,              -- what to do or say then
    created_at    text not null,
    expires_at    text not null,
    fires         integer not null default 0,
    last_fired_at text,
    cancelled     integer not null default 0
);
"""
SCHEMA += HAPPENINGS

# What the crew raised to the user on its own, at which level, and the mornings' digests (migration 8).
PROACTIVE = """
create table if not exists proactive (
    id           integer primary key autoincrement,
    at           text not null,
    bot          text not null,
    level        text not null,   -- fyi, question or needs_you
    text         text not null,
    source       text not null,   -- what raised it: checkin
    expires_at   text not null,
    delivered_at text,            -- when it reached the chat
    digest_at    text,            -- the digest it went in: that day's midnight, UTC
    reaction     text             -- the user's: up or down
);
create table if not exists digests (
    day text primary key,         -- midnight, UTC, of the day it was for
    at  text not null
);
"""
SCHEMA += PROACTIVE

# The chats you pinned to the top of the sidebar, in your order: kept here so a reinstall of the app keeps them.
PINS = """
create table if not exists pins (
    key      text primary key,   -- a chat's: bot:<name> or room:<id>
    position integer not null
);
"""
SCHEMA += PINS

CHAT_MUTES = """
create table if not exists chat_mutes (
    bot text primary key
);
"""
SCHEMA += CHAT_MUTES

WAKES = """
create table if not exists wakes (  -- an always-on bot's own looks around (runner/wakes.py)
    bot       text primary key,
    looked_at text,              -- when it last looked: what happened since is news
    next_at   text,              -- when it asked to look again, if it did
    next_why  text
);
"""
SCHEMA += WAKES

BROUGHT_IN = """
create table if not exists brought_in (  -- memories from Claude Code and Codex the chief has learned from
    path        text primary key,
    fingerprint text not null,  -- of what it said then: a different one now means it's changed since
    at          text not null
);
"""
SCHEMA += BROUGHT_IN

PERMISSIONS = """
create table if not exists permissions (  -- what a bot asked the user to allow (runner/permissions.py), and the answer
    id          integer primary key autoincrement,
    task_id     text not null references tasks(id),
    kind        text not null,             -- action, folder or network
    folder      text not null default '',  -- for a folder: where it may write
    what        text not null,             -- what it asked for, in its words
    why         text not null default '',
    asked_at    text not null,
    allowed     integer,                   -- 1 or 0 once the user answers
    answered_at text
);
create index if not exists permissions_by_task on permissions(task_id);
"""
SCHEMA += PERMISSIONS

SETTINGS = """
create table if not exists crew_settings (
    key text primary key,
    value integer not null
);
"""
SCHEMA += SETTINGS

# Full-text search over what the crew said and decided: the `recall` tool. Kept in step with events as they're added.
# `search` matches words (stemmed); `trigrams`, the same rows, matches any three or more characters, for ids, paths
# and parts of code names (migration 6).
TRIGRAMS = """
create virtual table if not exists trigrams using fts5(
    text, task_id unindexed, event_id unindexed, kind unindexed, at unindexed, tokenize = 'trigram'
);
"""
SEARCH = """
create virtual table search using fts5(
    text, task_id unindexed, event_id unindexed, kind unindexed, at unindexed, tokenize = 'porter unicode61'
);
""" + TRIGRAMS


# Columns added before migrations were numbered: the first step adds whichever an older database lacks.
ADDED_COLUMNS = {
    "tasks": {
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
    },
    "room_messages": {
        "attachments": "alter table room_messages add column attachments text",  # JSON list, like a message event's
    },
}


class Connection:
    """The store's one SQLite connection, shared by the event loop and the threads some routes read from (analytics,
    code review, the scheduler). One statement at a time, each read to the end before the next: Python's sqlite3
    shares prepared statements between cursors, so two threads stepping at once fail with "bad parameter or other API
    misuse"."""

    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()

    def execute(self, sql: str, args: tuple[Any, ...] | list[Any] = ()) -> "Rows":
        with self._lock:
            cursor = self._conn.execute(sql, args)
            return Rows(cursor.fetchall(), cursor.lastrowid, cursor.rowcount)

    def executemany(self, sql: str, rows: list[Any] | Any) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


@dataclass
class Rows:
    """A statement's result, read in full: its rows, and what an insert or update reports."""

    rows: list[sqlite3.Row]
    lastrowid: int | None
    rowcount: int

    def fetchall(self) -> list[sqlite3.Row]:
        return self.rows

    def fetchone(self) -> sqlite3.Row | None:
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)



def _catch_up(db: Connection, index_history: Callable[[], None]) -> None:
    """Step 1: everything added before migrations were numbered (up to 26 October 2026). Tables a database lacks
    are made, columns it lacks are added, and the search index is built from its history if it has none."""
    db.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():
        have = {r["name"] for r in db.execute(f"pragma table_info({table})")}
        for column, sql in columns.items():
            if column not in have:
                db.execute(sql)
    if not db.execute("select 1 from sqlite_master where name = 'search'").fetchone():
        db.executescript(SEARCH)
        index_history()


def _better_recall(db: Connection) -> None:
    """Step 6: the trigram index, filled from the word index's rows (unless step 1 just built both), and the recall
    log."""
    db.executescript(TRIGRAMS + RECALL_LOG)
    if not db.execute("select 1 from trigrams limit 1").fetchone():
        db.execute("insert into trigrams (text, task_id, event_id, kind, at) select text, task_id, event_id, kind, at "
                   "from search")


def _add_columns(table: str, columns: dict[str, str],
                 then: str | None = None) -> Callable[[Connection, Callable[[], None]], None]:
    """A step that adds columns to a table, each unless it's there already (a table step 1 made has today's), and then
    runs `then`, to fill them in."""
    def step(db: Connection, _: Callable[[], None]) -> None:
        have = {r["name"] for r in db.execute(f"pragma table_info({table})")}
        for column, sql in columns.items():
            if column not in have:
                db.execute(f"alter table {table} add column {column} {sql}")
        if then:
            db.execute(then)
    return step


# Each step runs once, in order, on a database that hasn't had it: (what it does, the step).
MIGRATIONS: list[tuple[str, Callable[[Connection, Callable[[], None]], None]]] = [
    ("everything up to 26 October 2026", _catch_up),
    ("a group chat's own reply limit, and where its bots stopped", _add_columns("rooms", {
        "max_depth": "integer", "stopped": "text"})),
    ("muting and archiving group chats, and marking the crew's own", _add_columns("rooms", {
        "muted": "integer not null default 0", "archived_at": "text", "crew": "integer not null default 0"},
        then="update rooms set crew = 1 where id = (select id from rooms where title = 'Crew' order by created_at)")),
    ("a log of what bots do to each other", lambda db, _: db.executescript(AGENT_LOG)),
    ("what each task's session started with", lambda db, _: db.executescript(PRIMED)),
    ("trigram search, and a log of recall's queries", lambda db, _: _better_recall(db)),
    ("crew events, and standing intents", lambda db, _: db.executescript(HAPPENINGS)),
    ("what the crew raised on its own, and digests", lambda db, _: db.executescript(PROACTIVE)),
    ("chats pinned to the top of the sidebar", lambda db, _: db.executescript(PINS)),
    ("an always-on bot's looks around", lambda db, _: db.executescript(WAKES)),
    ("memories brought in from Claude Code and Codex", lambda db, _: db.executescript(BROUGHT_IN)),
    ("what bots ask the user to allow", lambda db, _: db.executescript(PERMISSIONS)),
    ("crew settings", lambda db, _: db.executescript(SETTINGS)),
    ("clear stops from the old default room reply limit",
     lambda db, _: db.execute("update rooms set stopped = null where max_depth is null")),
    ("direct chat mute settings", lambda db, _: db.executescript(CHAT_MUTES)),
]


def migrate(db: Connection, index_history: Callable[[], None]) -> None:
    """Brings the database up to date. `index_history` fills the search index from the events already stored."""
    if not db.execute("select 1 from sqlite_master where name = 'tasks'").fetchone():
        # A new database: today's layout, so every step is already done. Made in one transaction, so it's all there or
        # none of it is, and written once rather than once a table.
        db.executescript(f"begin;\n{SCHEMA}{SEARCH}\npragma user_version = {len(MIGRATIONS)};\ncommit;")
        return
    done = db.execute("pragma user_version").fetchone()[0]
    for number, (_, step) in enumerate(MIGRATIONS, start=1):
        if done < number:
            step(db, index_history)
            db.execute(f"pragma user_version = {number}")
