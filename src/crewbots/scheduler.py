"""The daemon's scheduler: starts routines when their time comes, and GitHub triggers when something new happens.

It never runs a burst of catch-ups. A schedule missed while the Mac slept (or the daemon was stopped, or the crew
paused) runs once when it can, and one whose last message still waits in its bot's chat is skipped that time. A trigger starts
on what's new since it was added, never on what was already there, and once per pull request version.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crewbots import checkins, paths, prompts, sources, timefmt
from crewbots import crew as crew_module
from crewbots import github as github_module
from crewbots import routines as routines_module
from crewbots.attention import BREAK_S
from crewbots.crew import NO_REPLY
from crewbots.routines import Routine, RoutineError
from crewbots.runner import Runner
from crewbots.runner.common import plan
from crewbots.runner.wakes import NEWS_GAP, URGENT_GAP
from crewbots.store import Store, Task

TICK_S = 30.0
STALL_EVERY_S = 180.0  # how often bots with open work and nothing running are looked for
GITHUB_EVERY_S = 300.0
MAX_STARTS_PER_POLL = 3  # per trigger; the rest wait for the next poll
FIRST_SINCE = timedelta(days=1)  # what a routine's first run looks back over
ACTIVITY_CHARS = 30_000
CORRECTION_CHARS = 12_000
CHECKIN_CHARS = 4_000  # of what changed, for a check-in: its context stays small
WAKE_CHARS = 6_000  # of what the crew did, for the chief's look around
SOURCES_EVERY_S = 60.0  # how often the user's workspaces and watched folders are looked at for events
AWAY = timedelta(days=7)  # with nothing from the user for this long, routines and check-ins wait
# A trigger's fields that someone outside the crew wrote, and what each is.
UNTRUSTED = {"pr.title": "pull request title", "pr.branch": "branch name", "pr.diff": "pull request diff",
             "pr.failures": "failed checks"}
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
                 github: Any = github_module, sources_every_s: float = SOURCES_EVERY_S):
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
        self.sources = sources.Sources()
        self.sources_every_s = sources_every_s
        self._watched: float | None = None
        self._stall_checked: float | None = None
        self._first_seen: dict[str, str] = {}  # bot → when its checklist was first looked at, since this start
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
        self.runner.greet()  # a crew not started off yet: the chief's first word, once
        if self.runner.paused:
            return []  # nothing is marked checked, so what came due while paused runs once on resume
        now = now or datetime.now().astimezone()
        await self.runner.capacity.refresh()
        started = []
        away = self._away(now)
        for routine in self.routines if not away else ():
            if routine.enabled and routine.schedule and (task := self._check_schedule(routine, now)):
                started.append(task)
        for bot in self.runner.crew.bots:  # overnight, while the crew is idle: one bot's memory at a time
            if self.runner.dream_due(bot, now):
                self.runner.dream(bot)
                break
        loop = asyncio.get_running_loop()
        if self._watched is None or loop.time() - self._watched >= self.sources_every_s:
            self._watched = loop.time()
            for event in await asyncio.to_thread(self._look_around):
                self.runner.happened(event.kind, event.subject, event.text)
        if self._stall_checked is None or loop.time() - self._stall_checked >= STALL_EVERY_S:
            self._stall_checked = loop.time()
            self.runner.stall_check(now)
        self.runner.flush_chief_events(now)
        self.runner.free_apps()
        self.runner.close_idle_editors()
        if not away:
            self._check_in_due(now)
            self._wake_due(now)
        self.runner.attention.tick(now)
        triggers = [r for r in self.routines if r.enabled and r.on and not away
                    and self.runner.crew.bots.get(r.bot)
                    and getattr(self.runner.backends[self.runner.crew.bots[r.bot].backend], "automatic_turns", True)]
        if (triggers or self._wants_github()) and (self._polled is None or loop.time() - self._polled >= self.github_every_s):
            self._polled = loop.time()
            started += await self._poll_github(triggers, now)
        return started

    def _away(self, now: datetime) -> bool:
        """Whether the user has been away AWAY or longer: then routines and check-ins wait, and the chief's chat says so
        once, until they write again."""
        last = self.store.last_user_message()
        away = bool(last) and now - datetime.fromisoformat(last) >= AWAY
        said = (self.store.routine_state("away") or {}).get("last_run_at")
        if away and not said and any(r.enabled for r in self.routines):
            crew = self.runner.crew
            chief = self.runner.chief_bot()
            if chief:
                chat = self.runner.chat(chief.name)
                self.runner._record(chat.id, kind="text", data={"away": True, "text": prompts.text(
                    "away", crew.home, since=timefmt.to_local(last).strftime("%a %d %b"))})
                self.store.routine_ran("away", now.isoformat(timespec="seconds"), chat.id)
        elif not away and said:
            self.store.routine_ran("away", "", "")  # back: routines carry on, and next time it's said again
        return away

    def _look_around(self) -> list[sources.CrewEvent]:
        """New commits and branches in the user's workspaces, and changes in watched folders (run off the loop: it
        runs git)."""
        crew = self.runner.crew
        recent = self.store.list_tasks(limit=200)
        repos = sources.repos([b.workspace for b in crew.bots.values()] + [p.folder for p in crew.projects.values()]
                              + [Path(t.repo) for t in recent if t.repo],
                              self.runner.worktree_root or paths.worktrees())
        folders = {f for bot in crew.bots if (c := checkins.load(crew.home, bot)) for f in c.watch if f.is_dir()}
        return self.sources.git(repos) + self.sources.folders(sorted(folders))

    def _wants_github(self) -> bool:
        """Whether anything waits on GitHub's events: a standing intent, or a checklist item on one."""
        github = {"review_requested", "ci_failed", "ci_passed"}
        crew = self.runner.crew
        return (any(i["kind"] in github for i in self.store.intents())
                or any(item.on in github for bot in crew.bots if (c := checkins.load(crew.home, bot)) for item in c.items))

    def _check_in_due(self, now: datetime) -> Task | None:
        """One bot's check-in, when something on its checklist is due, within its active hours, while it's idle and
        there's spare capacity. One a tick, so they never pile up."""
        for bot, card in self.runner.crew.bots.items():
            checklist = checkins.load(self.runner.crew.home, bot)
            if not checklist or not checklist.active(now) or self.runner.bot_busy(bot):
                continue
            last = {i.id: (self.runner.store.routine_state(f"checkin:{bot}:{i.id}") or {}).get("last_run_at")
                    for i in checklist.items}
            first = self._first_seen.setdefault(bot, now.isoformat())  # what happened before isn't news to an item
            due = checklist.due(last, now, self.store.crew_events(_utc(now - timedelta(days=1))), first)
            if due and self.runner.capacity.verdict(plan(card.backend)).go:
                return self.check_in(bot, due, now)
        return None

    def _wake_due(self, now: datetime) -> Task | None:
        """A coordinator's look around on its own (runner/wakes.py), when it's worth one: the time it asked for has
        come, or there's news and it's been NEWS_GAP since its last look (URGENT_GAP for something failing). Never
        while it's working, while the user is mid-conversation, or in quiet hours unless something's urgent. A card
        with `proactive: false` never looks. One a tick."""
        runner = self.runner
        for bot, card in runner.crew.bots.items():
            if not runner.always_on(bot) or runner.bot_busy(bot):
                continue
            state = self.store.wake_state(bot)
            if not state["looked_at"]:  # from now on, what happens is news
                runner.mark_looked(bot)
                continue
            looked = datetime.fromisoformat(state["looked_at"])
            due = bool(state["next_at"]) and datetime.fromisoformat(state["next_at"]) <= now
            events, stopped, urgent, snap = runner.news_for(bot)
            news = bool(events or stopped) and now - looked >= (URGENT_GAP if urgent else NEWS_GAP)
            if not (due or news) or (runner.attention.quiet(now) and not urgent):
                continue
            if time.time() - runner.attention.user_wrote < BREAK_S or not runner.capacity.verdict(plan(card.backend)).go:
                continue
            return runner.wake(bot, self._wake_text(bot, state, events, stopped, looked, now, due), snap)
        return None

    def _wake_text(self, bot: str, state: dict[str, Any], events: list[dict[str, Any]], stopped: list[Task],
                   looked: datetime, now: datetime, due: bool) -> str:
        reason = (f"You asked to look again now: {state['next_why'] or 'no reason given'}." if due else
                  f"Something new since you last looked, at {_human(looked)}.")
        lines = _crew_lines(events)
        lines += [f"- {t.id} {t.bot}'s {t.title!r} is {t.status.value.replace('_', ' ')}" for t in stopped]
        seen = "\n".join(_crew_lines([e for e in self.store.crew_events(_utc(looked)) if e not in events]))
        news = ("# New\n\n" + prompts.untrusted("events", "\n".join(lines)) + "\n\n" if lines else "") + (
            "# Also\n\n" + prompts.untrusted("events", seen) + "\n\n" if seen else "") + activity(
            self.store, looked, chars=WAKE_CHARS)
        return prompts.text("wake", self.runner.crew.home, now=_human(now), reason=reason, news=news,
                            no_reply=NO_REPLY)

    def check_in(self, bot: str, items: list[checkins.Item] | None = None, now: datetime | None = None) -> Task:
        """Checks `bot` in on `items` (default: its whole checklist), with what the crew did since its last one."""
        now = now or datetime.now().astimezone()
        checklist = checkins.load(self.runner.crew.home, bot)
        if items is None and not checklist:
            raise ValueError(f"{bot} has no checklist: write one in {checkins.path(self.runner.crew.home, bot)}")
        last = (self.runner.store.routine_state(f"checkin:{bot}") or {}).get("last_run_at")
        since = datetime.fromisoformat(last) if last else now - timedelta(days=1)
        happened = "\n".join(_crew_lines(self.store.crew_events(_utc(since))))
        changes = ("# Events\n\n" + prompts.untrusted("events", happened) + "\n\n" if happened else "") + activity(
            self.runner.store, since, chars=CHECKIN_CHARS)
        return self.runner.check_in(bot, items if items is not None else checklist.items, changes, now)

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
        bot = self.runner.crew.bots.get(routine.bot)
        if bot and not getattr(self.runner.backends[bot.backend], "automatic_turns", True):
            return None
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
        if values is None:
            state = self.store.routine_state(routine.name) or {}
            since = datetime.fromisoformat(state["last_run_at"]) if state.get("last_run_at") else self._first_since(
                routine, now)
            values = {"since": _human(since), "now": _human(now)}
            if routine.wants("activity"):
                values["activity"] = activity(self.store, since, exclude_routine=routine.name)
            if routine.wants("corrections"):
                values["corrections"] = corrections(self.store, since)
        # What a pull request's author wrote reaches the bot as data from outside, never as instructions.
        goal = routine.render({k: prompts.untrusted(UNTRUSTED[k], str(v)) if k in UNTRUSTED else v for k, v in values.items()})
        if routine.schedule:  # a scheduled routine is a message in its bot's chat, from the routine
            task = self.runner.say(routine.bot, f"[routine: {routine.name}]\n\n{goal}", sender=f"routine:{routine.name}")
        else:  # a trigger is work: a thread under its bot's chat, which hears how it went
            chat = self.runner.chat(routine.bot)
            name = routine.name.replace("-", " ").replace("_", " ").capitalize()
            title = f"{name}: {values['pr.repo']}#{values['pr.number']} {values['pr.title']}"
            task = self.runner.submit(routine.bot, goal, created_by="routine", parent_id=chat.id,
                                      workspace=str(routine.workspace) if routine.workspace else None,
                                      routine=routine.name, budget=routine.budget, title=title)
        self.store.routine_ran(routine.name, now.isoformat(), task.id)
        return task

    def _first_since(self, routine: Routine, now: datetime) -> datetime:
        """What a first run looks back over: since the time before this one it would have run (Friday's standup for
        Monday's, last week for a retro)."""
        assert routine.schedule
        before = routine.schedule.due(now - routines_module.LOOKBACK, now - timedelta(minutes=1))
        return before or now - FIRST_SINCE

    # GitHub.

    async def _poll_github(self, triggers: list[Routine], now: datetime) -> list[Task]:
        try:
            snapshot = await asyncio.to_thread(self.github.fetch)
        except Exception as e:
            self.github_error = str(e)
            return []
        self.github_error = None
        for event in self.sources.github(snapshot):
            self.runner.happened(event.kind, event.subject, event.text)
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
    return _fit("\n\n".join(sections), chars)


def corrections(store: Store, since: datetime, chars: int = CORRECTION_CHARS) -> str:
    """What the crew got wrong since `since`, for the retro: each correction a bot noted when the user corrected it,
    then each review finding, under the bot whose work it was, so a mistake that came back shows as one."""
    events = store.events_since(_utc(since), ("correction", "review"), None)
    noted = [e for e in events if e.kind == "correction"]
    # A verdict is recorded on the review and on the work: the work's copy is the one counted.
    found = [e for e in events if e.kind == "review" and e.task_id == e.data.get("work_id") and e.data.get("findings")]
    if not noted and not found:
        return "No bot noted a correction, and no review found anything, in that time."
    sections = []
    if noted:
        lines = [f"## Corrections the user made ({len(noted)})"]
        for e in noted:
            d = e.data
            line = f"- {d.get('bot', '?')}, {_human(datetime.fromisoformat(e.at))}, in {e.task_id}: {_clip(d.get('mistake', ''))}"
            line += f" Lesson: {_clip(d.get('lesson', ''))}"
            if d.get("check"):
                line += f" A check that would catch it: {_clip(d['check'])}"
            lines.append(line)
        sections.append("\n".join(lines))
    if found:
        by_bot: dict[str, list[str]] = {}
        for e in found:
            work = store.get_task(e.task_id)
            for f in e.data["findings"]:
                by_bot.setdefault(work.bot, []).append(
                    f"- {e.task_id} version {e.data.get('version')}, by {e.data.get('reviewer')}: "
                    f"{f.get('location', '?')}: {_clip(f.get('problem', ''))}")
        lines = [f"## Review findings ({sum(len(v) for v in by_bot.values())})"]
        for bot, items in by_bot.items():
            lines += [f"In {bot}'s work:", *items]
        sections.append("\n".join(lines))
    return _fit("\n\n".join(sections), chars)


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


def _clock(at: str) -> str:
    return timefmt.minute(at)[11:16]


def _human(t: datetime) -> str:
    return timefmt.to_local(t).strftime("%a %d %b %H:%M")


def _crew_lines(events: list[dict[str, Any]]) -> list[str]:
    return [f"- {_clock(e['at'])} {e['kind']}: {e['text']}" for e in events]


def _fit(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[:chars] + "\n… (more; use recall or the task ids to look closer)"


def _clip(text: str, limit: int = EVENT_CHARS) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
