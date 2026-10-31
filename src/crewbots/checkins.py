"""A bot's check-in, like a heartbeat: a checklist of things to look at now and then, each with how often.

    crew/bots/<bot>/checkin.md

    Active hours: 9-18
    Watch: ~/Documents/specs
    - every 2h: pull requests waiting on the user for over a day
    - every 1d: dependencies with a security advisory
    - on commit acme-web: what changed, if it touches the export
    - on file specs: a new or changed spec, and what it asks for

An item is due once its interval has passed since it was last checked, or, for one that's `on` an event
(`sources.py`: commit, branch, file, review_requested, ci_failed, ci_passed, thread_done), once such an event,
mentioning its words if it has any, has happened since. `Watch:` names a folder whose files make `file` events.

The scheduler checks a bot in only when something is due, within its active hours (8 to 20 unless it says), while
the bot is idle and there's spare capacity (`capacity.py`). A bot with no checklist, or an empty one, costs nothing.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from crewbots.sources import KINDS

ITEM = re.compile(r"^\s*[-*]\s*every\s+(\d+)\s*(m|min|minutes?|h|hours?|d|days?)\s*:\s*(.+?)\s*$", re.IGNORECASE)
ON_ITEM = re.compile(r"^\s*[-*]\s*on\s+([a-z_]+)(?:\s+([^:]+?))?\s*:\s*(.+?)\s*$", re.IGNORECASE)
WATCH = re.compile(r"^\s*watch\s*:\s*(.+?)\s*$", re.IGNORECASE)
HOURS = re.compile(r"^\s*active hours\s*:\s*(\d{1,2})(?::00)?\s*[-–]\s*(\d{1,2})(?::00)?\s*$", re.IGNORECASE)
DEFAULT_HOURS = (8, 20)
SHORTEST = timedelta(minutes=15)  # an item asked for more often than this is checked this often


@dataclass(frozen=True)
class Item:
    id: str  # from its text, so editing an item makes it a new one, due at once
    every: timedelta | None  # how often; None for one that's on an event
    what: str
    on: str | None = None  # the kind of event it's due on
    match: str = ""  # words that event must mention


@dataclass(frozen=True)
class Checklist:
    items: list[Item]
    hours: tuple[int, int] = DEFAULT_HOURS
    watch: tuple[Path, ...] = ()  # folders whose changes make `file` events

    def active(self, now: datetime) -> bool:
        start, end = self.hours
        return start <= now.hour < end if start < end else now.hour >= start or now.hour < end

    def due(self, last: dict[str, str | None], now: datetime, events: Sequence[dict] = (), since: str = "") -> list[Item]:
        """The items whose interval has passed since `last[item.id]` (an ISO time), or that never ran; and those on
        an event that happened (in `events`, crew events) since they last ran, or since `since` if they never did."""
        due = []
        for item in self.items:
            ran = last.get(item.id)
            if item.on:
                after = datetime.fromisoformat(ran) if ran else datetime.fromisoformat(since) if since else now
                if any(e["kind"] == item.on and datetime.fromisoformat(e["at"]) > after
                       and all(w in f"{e['subject']} {e['text']}".lower() for w in item.match.lower().split())
                       for e in events):
                    due.append(item)
            elif not ran or now - datetime.fromisoformat(ran) >= item.every:
                due.append(item)
        return due


def path(home: Path, bot: str) -> Path:
    return home / "bots" / bot / "checkin.md"


def load(home: Path, bot: str) -> Checklist | None:
    """The bot's checklist, or None without one (or with nothing on it)."""
    try:
        text = path(home, bot).read_text()
    except OSError:
        return None
    items, hours, watch = [], DEFAULT_HOURS, []
    for line in text.splitlines():
        if found := HOURS.match(line):
            hours = (int(found[1]) % 24, int(found[2]) % 24)
        elif found := WATCH.match(line):
            watch.append(Path(found[1]).expanduser())
        elif (found := ON_ITEM.match(line)) and found[1].lower() in KINDS:
            what = found[3]
            items.append(Item(id=hashlib.sha256(line.strip().encode()).hexdigest()[:10], every=None, what=what,
                              on=found[1].lower(), match=(found[2] or "").strip()))
        elif found := ITEM.match(line):
            n, unit, what = int(found[1]), found[2][0].lower(), found[3]
            every = max(SHORTEST, {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit])
            items.append(Item(id=hashlib.sha256(what.encode()).hexdigest()[:10], every=every, what=what))
    return Checklist(items, hours, tuple(watch)) if items else None


def problems(home: Path, bot: str) -> list[str]:
    """The checklist's `on` items on an event there isn't, a typo say, which `load` skips: they'd never come due."""
    try:
        text = path(home, bot).read_text()
    except OSError:
        return []
    return [f"{line.strip()!r} is skipped: there's no {found[1]!r} event (there are {', '.join(KINDS)})"
            for line in text.splitlines() if (found := ON_ITEM.match(line)) and found[1].lower() not in KINDS]
