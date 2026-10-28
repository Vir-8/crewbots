"""Times as the user reads them. crewbots stores UTC; whatever a bot or the app is shown is converted here, in the crew's
time zone setting (the machine's own zone until it's set)."""

import os
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_chosen: str | None = None  # the crew's setting; None means the machine's zone


def valid(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return bool(name)


def set_zone(name: str | None) -> None:
    global _chosen
    _chosen = name if name and valid(name) else None


def system_zone() -> str:
    """The machine's zone as an IANA name: TZ if it names one, else where /etc/localtime points."""
    if (name := os.environ.get("TZ", "").lstrip(":")) and valid(name):
        return name
    try:
        name = str(Path("/etc/localtime").resolve()).split("zoneinfo/", 1)[1]
        if valid(name):
            return name
    except (IndexError, OSError):
        pass
    return "UTC"


def zone_name() -> str:
    return _chosen or system_zone()


def zone() -> tzinfo:
    return ZoneInfo(zone_name())


def to_local(at: str | datetime) -> datetime:
    """A stored UTC time (ISO text; one without an offset is UTC) in the crew's zone."""
    t = datetime.fromisoformat(at) if isinstance(at, str) else at
    return (t.replace(tzinfo=UTC) if t.tzinfo is None else t).astimezone(zone())


def now() -> datetime:
    return datetime.now(UTC).astimezone(zone())


def minute(at: str) -> str:
    """`2026-10-28T16:03`, for entries in a bot's notes and lists. A bare date stays as it is."""
    return at if len(at) <= 10 else to_local(at).strftime("%Y-%m-%dT%H:%M")


def stamp(at: str) -> str:
    """`2026-10-28T16:03:12+05:30`, for times in tool results, where the offset says which zone it is."""
    return at if len(at) <= 10 else to_local(at).isoformat(timespec="seconds")


def header() -> str:
    """The first line of every bot turn: what the clock says here and now."""
    t = now()
    offset = t.strftime("%z")
    return (f"[crewbots: it is {t:%A %d %b %Y, %H:%M} {t.tzname()} ({zone_name()}, UTC{offset[:3]}:{offset[3:]}). "
            "Times shown to you are in this zone.]")
