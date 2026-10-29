"""Routines: work the crew starts on its own, on a schedule or when something happens.

A routine is a Markdown file in the crew's `routines/` folder, like a role card. The frontmatter says when it runs
and who runs it; the body is the goal. A scheduled routine arrives as a message in its bot's chat; a trigger opens a
thread under that chat, which hears how it went, and can have a token budget for the thread:

    ---
    bot: chief
    schedule: "0 9 * * 1-5"      # cron, in the Mac's time zone: weekdays at 9:00
    ---
    Write the standup. Since {{since}}: {{activity}}

Instead of `schedule`, `on` names a trigger: `github.review_requested` (a pull request asks for your review) or
`github.ci_failed` (checks failed on one of your pull requests), in the repo named by `repo: owner/name`.
`{{...}}` fills in what the run is about; each kind of routine has its own fields (`FIELDS`).
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

ROUTINE_FIELDS = {"name", "bot", "schedule", "on", "repo", "workspace", "budget", "enabled", "description", "proposed_by"}
TRIGGERS = {"github.review_requested", "github.ci_failed"}
PR_FIELDS = {"pr.title", "pr.url", "pr.number", "pr.repo", "pr.author", "pr.branch", "pr.diff", "pr.failures"}
FIELDS = {
    "schedule": {"since", "now", "activity", "corrections"},
    "github.review_requested": PR_FIELDS - {"pr.failures"},
    "github.ci_failed": PR_FIELDS,
}
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_.]+)\s*\}\}")
LOOKBACK = timedelta(days=7)  # how far back a missed schedule is noticed, e.g. after a week away with the Mac shut


class RoutineError(ValueError):
    pass


@dataclass(frozen=True)
class Routine:
    name: str
    bot: str
    goal: str  # the body, with {{placeholders}}
    path: Path
    schedule: "Cron | None" = None
    on: str | None = None
    repo: str | None = None
    workspace: Path | None = None
    budget: int | None = None
    enabled: bool = True
    description: str = ""
    proposed_by: str | None = None  # the bot that proposed it: off until the user turns it on

    @property
    def when(self) -> str:
        if self.schedule:
            return self.schedule.text
        return f"on {self.on} in {self.repo}"

    def render(self, values: dict[str, Any]) -> str:
        return PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), self.goal).strip()

    def wants(self, field: str) -> bool:
        return any(m.group(1) == field for m in PLACEHOLDER.finditer(self.goal))


def load(routines_dir: Path, bots: set[str] | None = None) -> tuple[list[Routine], dict[str, str]]:
    """The crew's routines, and the files that couldn't be read (file name → why). One broken file never stops the
    others from running."""
    routines, errors = [], {}
    if not routines_dir.is_dir():
        return routines, errors
    for path in sorted(routines_dir.glob("*.md")):
        try:
            routine = parse(path.read_text(), path.stem, path)
            if bots is not None and routine.bot not in bots:
                raise RoutineError(f"no bot named {routine.bot!r}; the crew is {sorted(bots)}")
            routines.append(routine)
        except (RoutineError, yaml.YAMLError) as e:
            errors[path.name] = str(e)
    return routines, errors


def parse(text: str, default_name: str, source: Path) -> Routine:
    if not text.startswith("---"):
        raise RoutineError("a routine starts with --- frontmatter")
    try:
        _, raw, body = text.split("---", 2)
    except ValueError:
        raise RoutineError("the frontmatter has no closing ---") from None
    meta = yaml.safe_load(raw) or {}
    if not isinstance(meta, dict):
        raise RoutineError("frontmatter must be a mapping")
    if True in meta:  # YAML 1.1 reads a bare `on:` key as true, as GitHub Actions workflows also find
        meta["on"] = meta.pop(True)
    if unknown := set(meta) - ROUTINE_FIELDS:
        raise RoutineError(f"unknown fields {sorted(unknown)}; allowed: {sorted(ROUTINE_FIELDS)}")
    if "bot" not in meta:
        raise RoutineError("a routine needs a bot")
    if ("schedule" in meta) == ("on" in meta):
        raise RoutineError("a routine needs either a schedule or an on trigger, not both")
    on = meta.get("on")
    if on is not None and on not in TRIGGERS:
        raise RoutineError(f"on must be one of {sorted(TRIGGERS)}, not {on!r}")
    if on and not meta.get("repo"):
        raise RoutineError("a GitHub trigger needs a repo (owner/name), so it only starts on that repo's pull requests")
    goal = body.strip()
    if not goal:
        raise RoutineError("a routine needs a goal, in its body")
    allowed = FIELDS[on or "schedule"]
    if bad := sorted({m.group(1) for m in PLACEHOLDER.finditer(goal)} - allowed):
        raise RoutineError(f"{bad} can't be filled in here; this routine has {sorted(allowed)}")
    budget = meta.get("budget")
    if budget is not None and (not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0):
        raise RoutineError("budget is a number of tokens, for one run")
    if budget is not None and "schedule" in meta:
        raise RoutineError("a scheduled routine is a message in its bot's chat, so the bot's own budget covers it; "
                           "a per-run budget is for triggers, which open a thread")
    workspace = meta.get("workspace")
    return Routine(
        name=str(meta.get("name", default_name)), bot=str(meta["bot"]), goal=goal, path=source,
        schedule=Cron.parse(str(meta["schedule"])) if "schedule" in meta else None, on=on,
        repo=str(meta["repo"]) if meta.get("repo") else None,
        workspace=Path(workspace).expanduser() if workspace else None, budget=budget,
        enabled=bool(meta.get("enabled", True)) and not meta.get("proposed_by"), description=str(meta.get("description", "")),
        proposed_by=str(meta["proposed_by"]) if meta.get("proposed_by") else None,
    )


def starters() -> dict[str, str]:
    """The routines that ship with crewbots, by name: what `crewbots routines add` copies in."""
    folder = resources.files("crewbots") / "templates" / "crew" / "routines"
    return {f.name.removesuffix(".md"): f.read_text() for f in folder.iterdir() if f.name.endswith(".md")}


def add_starter(home: Path, name: str) -> Path:
    available = starters()
    if name not in available:
        raise RoutineError(f"no starter routine {name!r}; there are {sorted(available)}")
    path = home.expanduser() / "routines" / f"{name}.md"
    if path.exists():
        raise RoutineError(f"{path} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(available[name])
    return path


# Schedules.

NAMES = {
    3: {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"],
                                   start=1)},
    4: {d: i for i, d in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])},
}
RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # minute, hour, day of month, month, day of week (0 and 7: Sunday)
ALIASES = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}


@dataclass(frozen=True)
class Cron:
    """A five-field cron schedule, in local time. As in cron, when both the day of the month and the day of the week
    are restricted, a day matching either one runs."""

    text: str
    fields: tuple[frozenset[int], ...]
    any_day: bool  # the day of the month is *
    any_weekday: bool  # the day of the week is *

    @classmethod
    def parse(cls, text: str) -> "Cron":
        spec = ALIASES.get(text.strip(), text)
        parts = spec.split()
        if len(parts) != 5:
            raise RoutineError(f"schedule {text!r}: cron has five fields (minute hour day month weekday)")
        fields = tuple(_field(p, i, text) for i, p in enumerate(parts))
        weekdays = frozenset(d % 7 for d in fields[4])
        return cls(text=text, fields=(*fields[:4], weekdays), any_day=parts[2] == "*", any_weekday=parts[4] == "*")

    def matches(self, t: datetime) -> bool:
        minute, hour, day, month, weekday = self.fields
        if t.minute not in minute or t.hour not in hour or t.month not in month:
            return False
        on_day, on_weekday = t.day in day, (t.isoweekday() % 7) in weekday
        if self.any_day or self.any_weekday:
            return on_day and on_weekday
        return on_day or on_weekday

    def due(self, since: datetime, now: datetime) -> datetime | None:
        """The latest scheduled minute after `since`, up to `now`; None if there was none. However many were
        missed, there's one answer, so a Mac waking from a long sleep runs a routine once, not once per miss."""
        t = now.replace(second=0, microsecond=0)
        earliest = max(since, now - LOOKBACK)
        while t > earliest:
            if self.matches(t):
                return t
            t -= timedelta(minutes=1)
        return None

    def next(self, after: datetime, limit: timedelta = timedelta(days=366)) -> datetime | None:
        t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = after + limit
        while t <= end:
            if self.matches(t):
                return t
            t += _skip(self, t)
        return None


def _skip(cron: Cron, t: datetime) -> timedelta:
    """Whole hours or days that can't match are skipped, so finding the next run stays quick."""
    _, hour, _, month, _ = cron.fields
    if t.month not in month or not _day_matches(cron, t):
        return timedelta(days=1) - timedelta(hours=t.hour, minutes=t.minute)
    if t.hour not in hour:
        return timedelta(hours=1) - timedelta(minutes=t.minute)
    return timedelta(minutes=1)


def _day_matches(cron: Cron, t: datetime) -> bool:
    _, _, day, _, weekday = cron.fields
    on_day, on_weekday = t.day in day, (t.isoweekday() % 7) in weekday
    return on_day and on_weekday if cron.any_day or cron.any_weekday else on_day or on_weekday


def _field(part: str, index: int, text: str) -> frozenset[int]:
    low, high = RANGES[index]
    names = NAMES.get(index, {})
    values: set[int] = set()
    for item in part.lower().split(","):
        step = 1
        if "/" in item:
            item, step_text = item.split("/", 1)
            if not step_text.isdigit() or int(step_text) == 0:
                raise RoutineError(f"schedule {text!r}: bad step in {part!r}")
            step = int(step_text)
        if item == "*":
            start, end = low, high
        elif "-" in item:
            a, b = item.split("-", 1)
            start, end = _value(a, names, text), _value(b, names, text)
        else:
            start = _value(item, names, text)
            end = high if step > 1 else start
        if not (low <= start <= high and low <= end <= high and start <= end):
            raise RoutineError(f"schedule {text!r}: {part!r} is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


def _value(text: str, names: dict[str, int], spec: str) -> int:
    if text in names:
        return names[text]
    if not text.isdigit():
        raise RoutineError(f"schedule {spec!r}: {text!r} is not a number")
    return int(text)
