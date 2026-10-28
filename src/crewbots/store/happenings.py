"""What happened around the crew (commits, review requests, checks, files, finished threads: `sources.py`), the
standing intents waiting for it, and what the crew raised to the user on its own (`attention.py`)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from crewbots.store.records import _now

INTENT_DAYS = 90  # a standing intent expires after this
INTENT_FIRES = 3  # and stops after firing this many times
INTENT_COOLDOWN = timedelta(hours=24)  # and fires at most once in this long


class Happenings:
    """The part of Store for crew events and standing intents."""

    def add_crew_event(self, kind: str, subject: str, text: str) -> dict[str, Any]:
        cur = self.db.execute("insert into crew_events (at, kind, subject, text) values (?, ?, ?, ?)",
                              (_now(), kind, subject, text))
        return dict(self.db.execute("select * from crew_events where id = ?", (cur.lastrowid,)).fetchone())

    def crew_events(self, since: str = "", limit: int = 200) -> list[dict[str, Any]]:
        """Crew events at or after `since` (an ISO time, UTC), oldest first; the newest `limit` of them."""
        rows = self.db.execute("select * from crew_events where at >= ? order by id desc limit ?", (since, limit))
        return [dict(r) for r in reversed(rows.fetchall())]

    def events_between(self, after_id: int, up_to_id: int, kinds: set[str]) -> list[dict[str, Any]]:
        """The crew events of these kinds with `after_id` < id <= `up_to_id`, oldest first, all of them."""
        marks = ", ".join("?" * len(kinds))
        rows = self.db.execute(f"select * from crew_events where id > ? and id <= ? and kind in ({marks}) order by id",
                               (after_id, up_to_id, *sorted(kinds)))
        return [dict(r) for r in rows.fetchall()]

    def last_crew_event_id(self, at_or_before: str | None = None) -> int:
        """The newest crew event's id (0 if none), or the newest stamped at or before the given time."""
        row = self.db.execute("select max(id) from crew_events" + (" where at <= ?" if at_or_before else ""),
                              (at_or_before,) if at_or_before else ()).fetchone()
        return row[0] or 0

    def add_chief_event(self, kind: str, subject: str, who: str) -> None:
        self.db.execute("insert into chief_batch (at, kind, subject, who) values (?, ?, ?, ?)", (_now(), kind, subject, who))

    def chief_events(self) -> list[dict[str, Any]]:
        """Crew activity not yet sent to the chief, oldest first."""
        return [dict(r) for r in self.db.execute("select * from chief_batch order by id").fetchall()]

    def clear_chief_events(self, through: int) -> None:
        self.db.execute("delete from chief_batch where id <= ?", (through,))

    def add_intent(self, bot: str, task_id: str | None, kind: str, match: str, text: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        cur = self.db.execute(
            "insert into intents (bot, task_id, kind, match, text, created_at, expires_at) values (?, ?, ?, ?, ?, ?, ?)",
            (bot, task_id, kind, match, text, _now(), (now + timedelta(days=INTENT_DAYS)).isoformat(timespec="milliseconds")))
        return self.intent(cur.lastrowid)

    def intent(self, intent_id: int) -> dict[str, Any]:
        row = self.db.execute("select * from intents where id = ?", (intent_id,)).fetchone()
        if row is None:
            raise KeyError(intent_id)
        return dict(row)

    def intents(self, bot: str | None = None, active: bool = True) -> list[dict[str, Any]]:
        """Standing intents, the newest first: those still waiting (not cancelled, expired or fired out)."""
        rows = [dict(r) for r in self.db.execute("select * from intents order by id desc").fetchall()]
        now = _now()
        return [i for i in rows if (bot is None or i["bot"] == bot)
                and (not active or (not i["cancelled"] and i["expires_at"] > now and i["fires"] < INTENT_FIRES))]

    def intent_ready(self, intent: dict[str, Any]) -> bool:
        """Past its cooldown since it last fired."""
        last = intent["last_fired_at"]
        return not last or datetime.now(UTC) - datetime.fromisoformat(last) >= INTENT_COOLDOWN

    def fire_intent(self, intent_id: int) -> None:
        self.db.execute("update intents set fires = fires + 1, last_fired_at = ? where id = ?", (_now(), intent_id))

    def cancel_intent(self, intent_id: int) -> dict[str, Any]:
        self.db.execute("update intents set cancelled = 1 where id = ?", (intent_id,))
        return self.intent(intent_id)

    def add_proactive(self, bot: str, level: str, text: str, source: str, expires_at: str) -> dict[str, Any]:
        cur = self.db.execute("insert into proactive (at, bot, level, text, source, expires_at) values (?, ?, ?, ?, ?, ?)",
                              (_now(), bot, level, text, source, expires_at))
        return self.proactive_item(cur.lastrowid)

    def proactive_item(self, item_id: int) -> dict[str, Any]:
        row = self.db.execute("select * from proactive where id = ?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return dict(row)

    def proactive_waiting(self, level: str) -> list[dict[str, Any]]:
        """What was raised at `level` and hasn't reached the user yet (in the chat, or a digest), oldest first."""
        rows = self.db.execute("select * from proactive where level = ? and delivered_at is null and digest_at is null"
                               " order by id", (level,))
        return [dict(r) for r in rows]

    def mark_delivered(self, item_id: int) -> None:
        self.db.execute("update proactive set delivered_at = ? where id = ?", (_now(), item_id))

    def delivered_since(self, level: str, since: str) -> int:
        return self.db.execute("select count(*) from proactive where level = ? and delivered_at >= ?",
                               (level, since)).fetchone()[0]

    def digested_today(self, day: str) -> bool:
        return self.db.execute("select 1 from digests where day = ?", (day,)).fetchone() is not None

    def mark_digested(self, day: str, item_ids: list[int]) -> None:
        self.db.execute("insert or ignore into digests (day, at) values (?, ?)", (day, _now()))
        self.db.executemany("update proactive set digest_at = ? where id = ?", [(day, i) for i in item_ids])

    def react_proactive(self, item_id: int, reaction: str) -> dict[str, Any]:
        self.db.execute("update proactive set reaction = ? where id = ?", (reaction, item_id))
        return self.proactive_item(item_id)
