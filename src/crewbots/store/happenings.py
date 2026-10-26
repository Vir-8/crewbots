"""What happened around the crew (commits, review requests, checks, files, finished threads: `sources.py`), and the
standing intents waiting for it."""

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
