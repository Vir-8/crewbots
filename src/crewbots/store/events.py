"""Events: every one a task's run produced, read back by task, by time or as the newest; the fresh tokens they add up
to, for budgets and analytics; and the log of what bots did to each other."""

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

from crewbots.events import Event
from crewbots.store.records import StoredEvent, Task, _now, _task
from crewbots.store.tasks import TREE

AGENT_LOG_CHARS = 4000  # of each entry's text: a goal, a question, a message or a post


class Events:
    """The part of Store for events, the fresh tokens they add up to, and the agent log."""

    def log_agent(self, kind: str, bot: str, target: str | None = None, task_id: str | None = None,
                  room_id: str | None = None, text: str = "") -> None:
        """Records one thing a bot did to another (a delegation, review, question, message, post or group), or one
        of its tool calls that crewbots refused."""
        self.db.execute("insert into agent_log (at, kind, bot, target, task_id, room_id, text) values (?, ?, ?, ?, ?, ?, ?)",
                        (_now(), kind, bot, target, task_id, room_id, text[:AGENT_LOG_CHARS]))

    def agent_log(self, limit: int = 200, before: int | None = None) -> list[dict[str, Any]]:
        """The agent log, newest first; `before` an entry's id, for the page before it."""
        rows = self.db.execute("select * from agent_log where id < ? order by id desc limit ?",
                               (before if before is not None else 2**63 - 1, limit)).fetchall()
        return [dict(r) for r in rows]

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

    def bot_usage(self, bot: str, since: str) -> int:
        return self._fresh_tokens("join tasks t on t.id = e.task_id", "t.bot = ? and e.at >= ?", (bot, since))

    def tree_usage(self, root_id: str) -> int:
        """Tokens used by a task and everything it delegated, however deep."""
        return self._fresh_tokens("", f"e.task_id in ({TREE})", (root_id,))

    def _fresh_tokens(self, join: str, where: str, args: tuple[Any, ...]) -> int:
        fresh = ("json_extract(e.data, '$.input_tokens') - coalesce(json_extract(e.data, '$.cached_input_tokens'), 0)"
                 " + json_extract(e.data, '$.output_tokens')")
        sql = f"select coalesce(sum({fresh}), 0) from events e {join} where e.kind = 'usage' and {where}"
        return int(self.db.execute(sql, args).fetchone()[0])

    def activity(self, since: str, exclude_routine: str | None = None) -> list[Task]:
        """Threads of work with anything new since `since`, oldest first: each one that isn't inside another thread,
        leaving out one routine's own runs."""
        rows = self.db.execute(
            "select t.* from tasks t left join tasks p on p.id = t.parent_id where t.kind = 'thread' and t.pr is null"
            " and (p.id is null or p.kind != 'thread') and t.updated_at >= ? and coalesce(t.routine, '') != ?"
            " order by t.created_at, t.id", (since, exclude_routine or "")).fetchall()
        return [_task(r) for r in rows]

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
            " sum(coalesce(json_extract(e.data, '$.cached_input_tokens'), 0)) as cached,"
            " sum(coalesce(json_extract(e.data, '$.cache_write_tokens'), 0)) as written"
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
            "reviews": one("select count(*) from tasks where kind = 'review' and parent_id is null and created_at >= ?",
                           since),  # a review, not each of its reviewers
            "routine_runs": one("select count(*) from events where kind = 'message' and at >= ?"
                                " and json_extract(data, '$.from') like 'routine:%'", since),
            "room_messages": one("select count(*) from room_messages where at >= ?", since),
        }

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

    def last_user_message(self) -> str | None:
        """When the user last wrote anything, in a chat, a thread or a group chat."""
        said = self.db.execute("select max(at) from events where kind = 'message' and json_extract(data, '$.from') = 'user'"
                               ).fetchone()[0]
        posted = self.db.execute("select max(at) from room_messages where author = 'user'").fetchone()[0]
        return max(filter(None, (said, posted)), default=None)

    def last_events(self, task_id: str, kinds: tuple[str, ...], limit: int) -> list[StoredEvent]:
        """A task's last `limit` events of these kinds, oldest first."""
        rows = self.db.execute(f"select * from events where task_id = ? and kind in ({','.join('?' * len(kinds))}) "
                               "order by id desc limit ?", (task_id, *kinds, limit)).fetchall()
        return [StoredEvent(id=r["id"], task_id=r["task_id"], kind=r["kind"], data=json.loads(r["data"]), at=r["at"])
                for r in reversed(rows)]

    def events(self, task_id: str | None = None, after: int = 0, limit: int = 1000) -> list[StoredEvent]:
        if task_id:
            sql, args = "select * from events where task_id = ? and id > ? order by id limit ?", (task_id, after, limit)
        else:
            sql, args = "select * from events where id > ? order by id limit ?", (after, limit)
        return [
            StoredEvent(id=r["id"], task_id=r["task_id"], kind=r["kind"], data=json.loads(r["data"]), at=r["at"])
            for r in self.db.execute(sql, args).fetchall()
        ]
