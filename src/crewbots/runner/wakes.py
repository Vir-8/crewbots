"""Always on: a coordinator, the chief or another, looks around on its own whenever there's something worth a look,
rather than on a fixed timer. Something it should know about wakes it: a thread finishing, checks failing, a review
asked for, work that stopped on the user or failed. So does the time it chose itself with `next_check`: soon while
something's moving, later when it's quiet. Each look is a turn in its own chat, prompted by crewbots where the user
doesn't see it: with something to say, its reply shows as its own, tagged as said on its own; with nothing, it
answers NO_REPLY and the chat shows nothing at all.

The scheduler decides when (`Scheduler._wake_due`): never while the bot is working, while the user is mid-
conversation, or through quiet hours (unless something failed), and news alone at most every NEWS_GAP. Smaller things
(commits, branches, changed files) never wake it; they're in its next look. A coordinator whose card says
`proactive: false` doesn't look around on its own.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from crewbots import timefmt
from crewbots.runner.common import NotAllowed
from crewbots.store import STOPPED, Status, Task

NEWS_GAP = timedelta(minutes=20)  # news alone wakes it at most this often
URGENT_GAP = timedelta(minutes=5)  # and something failing, this often
SOONEST, LATEST = 5, 24 * 60  # minutes a bot may ask to look again in
WAKING = {"thread_done", "ci_failed", "review_requested"}  # crew events that wake it; the rest wait for its next look
URGENT = {"ci_failed"}
STOP_EVENT = "task_stopped"  # the crew event the store records when a task stops (store/tasks.py)


class Wakes:
    """The part of Runner that lets a coordinator look around on its own, unless its card says `proactive: false`."""

    def always_on(self, bot: str) -> bool:
        card = self.crew.bots.get(bot)
        return bool(card and self.is_coordinator(bot) and card.proactive)

    def next_check(self, task_id: str, minutes: float, why: str) -> datetime:
        """The `next_check` tool: when the bot wants to look around again, and what for."""
        task = self.store.get_task(task_id)
        if not self.always_on(task.bot):
            raise NotAllowed("only coordinators look around on their own")
        at = datetime.now(UTC) + timedelta(minutes=min(max(float(minutes), SOONEST), LATEST))
        self.store.set_wake(task.bot, next_at=at.isoformat(timespec="seconds"), next_why=" ".join(why.split())[:300])
        return timefmt.to_local(at)

    def mark_looked(self, bot: str, seen: int | None = None, **fields: str | None) -> None:
        """The bot looks now, having seen every crew event up to id `seen` (the newest, by default)."""
        self.store.set_wake(bot, looked_at=datetime.now(UTC).isoformat(timespec="milliseconds"),
                            seen_event=self.store.last_crew_event_id() if seen is None else seen, **fields)

    def news_for(self, bot: str) -> tuple[list[dict[str, Any]], list[Task], bool, int]:
        """What happened since the bot's last look that's worth it: crew events that wake it, other than its own
        threads finishing (their results reach it anyway), and work that stopped (a `task_stopped` event), other than
        its own, what it delegated (which reports to it), reviews and asks, and what has since got going again. And
        whether any of it is urgent, and the id of the newest event there is: the look is up to that one, so what is
        written after it is still news. News is by id, not time, which can tie. A look kept only as a time (from before
        ids were kept) is turned into an id once, treating all up to that time as seen."""
        state = self.store.wake_state(bot)
        if state["seen_event"] is None:
            self.store.set_wake(bot, looked_at=state["looked_at"] or "",
                                seen_event=self.store.last_crew_event_id(state["looked_at"] or ""))
            state = self.store.wake_state(bot)
        snap = self.store.last_crew_event_id()
        mine = {t.id for t in self.store.list_tasks(bot=bot, limit=500)}
        events: list[dict[str, Any]] = []
        stopped: list[Task] = []
        for e in self.store.events_between(state["seen_event"], snap, WAKING | {STOP_EVENT}):
            if e["kind"] == STOP_EVENT:
                try:
                    t = self.store.get_task(e["subject"])
                except KeyError:
                    continue
                if (t.status in STOPPED and t.bot != bot and t.created_by != bot and t.kind not in ("review", "ask")
                        and t not in stopped):
                    stopped.append(t)
            elif not (e["kind"] == "thread_done" and e["subject"] in mine):
                events.append(e)
        urgent = any(e["kind"] in URGENT for e in events) or any(t.status is Status.FAILED for t in stopped)
        return events, stopped, urgent, snap

    def _turn_start(self, task_id: str) -> None:
        """Marks where a logical turn begins, and whether the crew started it: only if every prompt in it is crewbots'
        own (a notice, watchdog, look-around, check-in). A turn carried on after a restart already has its mark."""
        system = task_id not in self._by_user
        self._by_user.discard(task_id)
        if task_id in self._continuing:
            self._continuing.discard(task_id)
        else:
            self._record(task_id, kind="turn_start", data={"system": system})

    def _resume_mark(self, task_id: str, started: bool, waiting: bool) -> None:
        """A turn resumed after a restart carries the user's interrupted request, so it isn't the crew's. If the bot had
        begun it, it continues the turn already recorded, unless that turn was marked the crew's and messages that came in
        meanwhile are folded into its prompt: some may be the user's, so it gets a fresh mark."""
        self._by_user.add(task_id)
        mark = self.store.last_event(task_id, ("turn_start",))
        if started and not (waiting and mark and mark.data.get("system")):
            self._continuing.add(task_id)

    def wake(self, bot: str, text: str, seen: int | None = None) -> Task:
        """A look around: a turn in the bot's chat, prompted by crewbots, whose reply the user sees as said on its
        own. The next one is whenever it asks for, or news comes. `seen` is the newest event the text was made from."""
        chat = self.chat(bot)
        self._waking.add(chat.id)
        self.mark_looked(bot, seen, next_at=None, next_why=None)
        return self.send(chat.id, text, sender="crewbots")
