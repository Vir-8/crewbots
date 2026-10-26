"""A bot's check-in, like a heartbeat: a checklist of things to look at now and then, each with how often.

    crew/bots/<bot>/checkin.md

    Active hours: 9-18
    - every 2h: pull requests waiting on the user for over a day
    - every 1d: dependencies with a security advisory

An item is due once its interval has passed since it was last checked. The scheduler checks a bot in only when
something is due, within its active hours (8 to 20 unless it says), while the bot is idle and there's spare capacity
(`capacity.py`). A bot with no checklist, or an empty one, costs nothing.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ITEM = re.compile(r"^\s*[-*]\s*every\s+(\d+)\s*(m|min|minutes?|h|hours?|d|days?)\s*:\s*(.+?)\s*$", re.IGNORECASE)
HOURS = re.compile(r"^\s*active hours\s*:\s*(\d{1,2})(?::00)?\s*[-–]\s*(\d{1,2})(?::00)?\s*$", re.IGNORECASE)
DEFAULT_HOURS = (8, 20)
SHORTEST = timedelta(minutes=15)  # an item asked for more often than this is checked this often


@dataclass(frozen=True)
class Item:
    id: str  # from its text, so editing an item makes it a new one, due at once
    every: timedelta
    what: str


@dataclass(frozen=True)
class Checklist:
    items: list[Item]
    hours: tuple[int, int] = DEFAULT_HOURS

    def active(self, now: datetime) -> bool:
        start, end = self.hours
        return start <= now.hour < end if start < end else now.hour >= start or now.hour < end

    def due(self, last: dict[str, str | None], now: datetime) -> list[Item]:
        """The items whose interval has passed since `last[item.id]` (an ISO time), or that never ran."""
        return [i for i in self.items if not last.get(i.id) or now - datetime.fromisoformat(last[i.id]) >= i.every]


def path(home: Path, bot: str) -> Path:
    return home / "bots" / bot / "checkin.md"


def load(home: Path, bot: str) -> Checklist | None:
    """The bot's checklist, or None without one (or with nothing on it)."""
    try:
        text = path(home, bot).read_text()
    except OSError:
        return None
    items, hours = [], DEFAULT_HOURS
    for line in text.splitlines():
        if found := HOURS.match(line):
            hours = (int(found[1]) % 24, int(found[2]) % 24)
        elif found := ITEM.match(line):
            n, unit, what = int(found[1]), found[2][0].lower(), found[3]
            every = max(SHORTEST, {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit])
            items.append(Item(id=hashlib.sha256(what.encode()).hexdigest()[:10], every=every, what=what))
    return Checklist(items, hours) if items else None
