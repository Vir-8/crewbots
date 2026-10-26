"""The daemon's scheduler: starts routines when their time comes, and GitHub triggers when something new happens.

It never runs a burst of catch-ups. A schedule missed while the Mac slept (or the daemon was stopped, or the crew
paused) runs once when it can, and one whose last message still waits in its bot's chat is skipped that time. A trigger starts
on what's new since it was added, never on what was already there, and once per pull request version.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from crewbots import checkins
from crewbots import crew as crew_module
from crewbots import github as github_module
from crewbots import routines as routines_module
from crewbots.routines import Routine, RoutineError
from crewbots.runner import Runner
from crewbots.store import Store, Task

TICK_S = 30.0
GITHUB_EVERY_S = 300.0
MAX_STARTS_PER_POLL = 3  # per trigger; the rest wait for the next poll
FIRST_SINCE = timedelta(days=1)  # what a routine's first run looks back over
ACTIVITY_CHARS = 30_000
CHECKIN_CHARS = 4_000  # of what changed, for a check-in: its context stays small
EVENT_CHARS = 400

log = logging.getLogger("crewbots.scheduler")


@dataclass(frozen=True)
class RoutineView:
    routine: Routine
    last_run_at: str | None
    last_task_id: str | None
    next_run_at: str | None
    skipped_at: str | None  # the last time it was due while its previous message still waited in the bot's chat


class Scheduler:
    def __init__(self, runner: Runner, *, tick_s: float = TICK_S, github_every_s: float = GITHUB_EVERY_S,
                 github: Any = github_module):
        self.runner = runner
        self.store: Store = runner.store
        self.tick_s = tick_s
        self.github_every_s = github_every_s
        self.github = github
        self.routines: list[Routine] = []
        self.errors: dict[str, str] = {}  # routine file → why it couldn't be read
        self.github_error: str | None = None
        self._polled: float | None = None
        self._skipped: dict[str, str] = {}
        self.crew_error: str | None = None  # a role card or team file that couldn't be read; the crew carries on as it was
        self._stamp = crew_module.stamp(runner.crew.home)

    def load(self) -> None:
        """Read every tick, so a routine file you add or edit counts without restarting anything."""
        self.routines, self.errors = routines_module.load(self.runner.crew.routines_dir, set(self.runner.crew.bots))

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.tick_s)

    def reload_if_changed(self) -> bool:
        """Edited role cards, team files and skills count from the next turn, with no reload to remember."""
        current = crew_module.stamp(self.runner.crew.home)
        if current == self._stamp:
            return False
        self._stamp = current
        try:
            self.runner.reload(crew_module.load(self.runner.crew.home))
        except crew_module.CrewError as e:
            self.crew_error = str(e)
            return False
        self.crew_error = None
        return True

    async def tick(self, now: datetime | None = None) -> list[Task]:
        self.reload_if_changed()
        self.load()
        if self.runner.paused:
            return []  # nothing is marked checked, so what came due while paused runs once on resume
        now = now or datetime.now().astimezone()
        await self.runner.capacity.refresh()
        started = []
        for routine in self.routines:
            if routine.enabled and routine.schedule and (task := self._check_schedule(routine, now)):
                started.append(task)
        for bot in self.runner.crew.bots:  # overnight, while the crew is idle: one bot's memory at a time
            if self.runner.dream_due(bot, now):
                self.runner.dream(bot)
                break
        self._check_in_due(now)
        triggers = [r for r in self.routines if r.enabled and r.on]
        loop = asyncio.get_running_loop()
        if triggers and (self._polled is None or loop.time() - self._polled >= self.github_every_s):
            self._polled = loop.time()
            started += await self._poll_github(triggers, now)
        return started

    def _check_in_due(self, now: datetime) -> Task | None:
        """One bot's check-in, when something on its checklist is due, within its active hours, while it's idle and
        there's spare capacity. One a tick, so they never pile up."""
        for bot, card in self.runner.crew.bots.items():
            checklist = checkins.load(self.runner.crew.home, bot)
            if not checklist or not checklist.active(now) or self.runner.bot_busy(bot):
                continue
            last = {i.id: (self.runner.store.routine_state(f"checkin:{bot}:{i.id}") or {}).get("last_run_at")
                    for i in checklist.items}
            due = checklist.due(last, now)
            if due and self.runner.capacity.verdict(card.backend.split("-")[0]).go:
                return self.check_in(bot, due, now)
        return None

    def check_in(self, bot: str, items: list[checkins.Item] | None = None, now: datetime | None = None) -> Task:
        """Checks `bot` in on `items` (default: its whole checklist), with what the crew did since its last one."""
        now = now or datetime.now().astimezone()
        checklist = checkins.load(self.runner.crew.home, bot)
        if items is None and not checklist:
            raise ValueError(f"{bot} has no checklist: write one in {checkins.path(self.runner.crew.home, bot)}")
        last = (self.runner.store.routine_state(f"checkin:{bot}") or {}).get("last_run_at")
        since = datetime.fromisoformat(last) if last else now - timedelta(days=1)
        changes = activity(self.runner.store, since, chars=CHECKIN_CHARS)
        return self.runner.check_in(bot, items if items is not None else checklist.items, changes)

    def views(self, now: datetime | None = None) -> list[RoutineView]:
        now = now or datetime.now().astimezone()
        views = []
        for r in self.routines:
            state = self.store.routine_state(r.name) or {}
            upcoming = r.schedule.next(now) if r.schedule and r.enabled else None
            views.append(RoutineView(routine=r, last_run_at=state.get("last_run_at"),
                                     last_task_id=state.get("last_task_id"),
                                     next_run_at=upcoming.isoformat() if upcoming else None,
                                     skipped_at=self._skipped.get(r.name)))
        return views

    def run_now(self, name: str) -> Task:
        self.load()
        routine = next((r for r in self.routines if r.name == name), None)
        if routine is None:
            raise RoutineError(self.errors.get(f"{name}.md") or f"no routine named {name!r}")
        if routine.on:
            raise RoutineError(f"{name} starts when GitHub has something new ({routine.on}); it can't be run by hand")
        return self._start(routine, datetime.now().astimezone())

    # Schedules.

    def _check_schedule(self, routine: Routine, now: datetime) -> Task | None:
        assert routine.schedule
        state = self.store.routine_state(routine.name)
        self.store.routine_checked(routine.name, now.isoformat())
        if state is None:
            return None  # new: it runs from its next scheduled time, not for times before it existed
        due = routine.schedule.due(datetime.fromisoformat(state["checked_at"]), now)
        if due is None:
            return None
        chat = self.store.chat_of(routine.bot)
        if chat and any(m.startswith(f"[routine: {routine.name}]") for m in self.runner.queued(chat.id)):
            self._skipped[routine.name] = now.isoformat()
            return None
        return self._start(routine, now)

    def _start(self, routine: Routine, now: datetime, values: dict[str, Any] | None = None) -> Task:
        name = routine.name.replace("-", " ").replace("_", " ").capitalize()
        title = f"{name}: {values['pr.repo']}#{values['pr.number']} {values['pr.title']}" if values else f"{name}, {now:%a %d %b}"
        if values is None:
            state = self.store.routine_state(routine.name) or {}
            since = datetime.fromisoformat(state["last_run_at"]) if state.get("last_run_at") else self._first_since(
                routine, now)
            values = {"since": _human(since), "now": _human(now)}
            if routine.wants("activity"):
                values["activity"] = activity(self.store, since, exclude_routine=routine.name)
        goal = routine.render(values)
        if routine.schedule:  # a scheduled routine is a message in its bot's chat, from the routine
            task = self.runner.say(routine.bot, f"[routine: {routine.name}]\n\n{goal}", sender=f"routine:{routine.name}")
        else:  # a trigger is work: a thread under its bot's chat, which hears how it went
            chat = self.runner.chat(routine.bot)
            task = self.runner.submit(routine.bot, goal, created_by="routine", parent_id=chat.id,
                                      workspace=str(routine.workspace) if routine.workspace else None,
                                      routine=routine.name, budget=routine.budget, title=title)
        self.store.routine_ran(routine.name, now.isoformat(), task.id)
        return task

    def _first_since(self, routine: Routine, now: datetime) -> datetime:
        """What a first run looks back over: since the time before this one it would have run (Friday's standup for
        Monday's, last week for a retro)."""
        before = routine.schedule.due(now - routines_module.LOOKBACK, now - timedelta(minutes=1)) if routine.schedule else None
        return before or now - FIRST_SINCE

    # GitHub.

    async def _poll_github(self, triggers: list[Routine], now: datetime) -> list[Task]:
        try:
            snapshot = await asyncio.to_thread(self.github.fetch)
        except Exception as e:
            self.github_error = str(e)
            return []
        self.github_error = None
        started = []
        for routine in triggers:
            prs = snapshot.review_requested if routine.on == "github.review_requested" else [
                p for p in snapshot.mine if p.failed or p.checks in ("FAILURE", "ERROR")]
            prs = [p for p in prs if p.repo.lower() == (routine.repo or "").lower()]
            first_poll = self.store.routine_state(routine.name) is None
            self.store.routine_checked(routine.name, now.isoformat())
            if first_poll:
                self.store.mark_triggered(routine.name, [p.key for p in prs])  # what was already there isn't new
                continue
            seen = self.store.triggered(routine.name, [p.key for p in prs])
            for pr in [p for p in prs if p.key not in seen][:MAX_STARTS_PER_POLL]:
                self.store.mark_triggered(routine.name, [pr.key])  # first, so a failure below never repeats it
                try:
                    values = await asyncio.to_thread(self._pr_values, routine, pr)
                    started.append(self._start(routine, now, values))
                except Exception as e:
                    log.warning("couldn't start %s for %s: %s", routine.name, pr.key, e)
        return started

    def _pr_values(self, routine: Routine, pr: github_module.PullRequest) -> dict[str, Any]:
        values: dict[str, Any] = {"pr.title": pr.title, "pr.url": pr.url, "pr.number": pr.number, "pr.repo": pr.repo,
                                  "pr.author": pr.author, "pr.branch": pr.branch}
        if routine.wants("pr.diff"):
            values["pr.diff"] = self.github.diff(pr)
        if routine.wants("pr.failures"):
            values["pr.failures"] = self.github.failures(pr)
        return values


def activity(store: Store, since: datetime, exclude_routine: str | None = None, chars: int = ACTIVITY_CHARS) -> str:
    """What the crew did since `since`, for a routine to read: each goal with anything new, what was delegated for it,
    and what was said, asked, reviewed and merged along the way."""
    goals = store.activity(_utc(since), exclude_routine=exclude_routine)  # the store's times are UTC, compared as text
    if not goals:
        return "Nothing happened in the crew in that time."
    sections = []
    for goal in goals:
        tree = store.tree(goal.id)
        bots = {t.id: t.bot for t in tree}
        head = f"{goal.id} · {goal.bot} · {goal.status}"
        if goal.routine:
            head += f" · from the routine {goal.routine}"
        if goal.merge_state:
            head += f" · {goal.merge_state}"
        lines = [f"## {goal.title}", head]
        lines += [f"- delegated {t.id} to {t.bot}: {t.title} ({t.status})" for t in tree[1:]]
        kinds = ("message", "escalation", "review", "merged", "held", "rate_limit")
        for e in store.events_since(_utc(since), kinds, list(bots)):
            line = _event_line(e.kind, e.data, bots.get(e.task_id, "?"))
            if line:
                lines.append(line)
        if goal.result:
            lines.append(f"Latest reply: {_clip(goal.result, EVENT_CHARS * 2)}")
        sections.append("\n".join(lines))
    text = "\n\n".join(sections)
    if len(text) > chars:
        text = text[:chars] + "\n… (more; use recall or the task ids to look closer)"
    return text


def _event_line(kind: str, data: dict[str, Any], bot: str) -> str | None:
    match kind:
        case "message" if data.get("from") == "user":
            return f"- you said: {_clip(data.get('text', ''))}"
        case "escalation":
            return f"- {bot} asked: {_clip(data.get('question', ''))}"
        case "review":
            findings = "; ".join(f"{f.get('location', '?')}: {f.get('problem', '')}" for f in data.get("findings") or [])
            return (f"- {data.get('reviewer', bot)} reviewed version {data.get('version')}: "
                    f"{str(data.get('verdict', '')).upper()}, {_clip(data.get('summary', ''))}"
                    + (f" Findings: {_clip(findings)}" if findings else ""))
        case "merged":
            return f"- you merged it ({str(data.get('commit', ''))[:12]})"
        case "held":
            return f"- held: {data.get('reason', '')}"
        case "rate_limit":
            return f"- the {data.get('plan')} plan's {data.get('window')} limit: {data.get('status')}"
    return None


def _utc(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="milliseconds")


def _human(t: datetime) -> str:
    return t.astimezone().strftime("%a %d %b %H:%M")


def _clip(text: str, limit: int = EVENT_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
