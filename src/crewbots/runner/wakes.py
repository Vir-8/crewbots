"""Always on: the crew's coordinator (the chief) looks around on its own whenever there's something worth a look,
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

from crewbots.runner.common import NotAllowed
from crewbots.store import Status, Task

NEWS_GAP = timedelta(minutes=20)  # news alone wakes it at most this often
URGENT_GAP = timedelta(minutes=5)  # and something failing, this often
SOONEST, LATEST = 5, 24 * 60  # minutes a bot may ask to look again in
WAKING = {"thread_done", "ci_failed", "review_requested"}  # crew events that wake it; the rest wait for its next look
URGENT = {"ci_failed"}
STOPPED = {Status.FAILED, Status.NEEDS_INPUT, Status.HELD}  # work that stopped, which may need it


class Wakes:
    """The part of Runner that lets the chief look around on its own."""

    def always_on(self, bot: str) -> bool:
        card = self.crew.bots.get(bot)
        return bool(card and card.can_delegate and card.proactive)

    def next_check(self, task_id: str, minutes: float, why: str) -> datetime:
        """The `next_check` tool: when the bot wants to look around again, and what for."""
        task = self.store.get_task(task_id)
        if not self.always_on(task.bot):
            raise NotAllowed("only the crew's coordinator looks around on its own")
        at = datetime.now(UTC) + timedelta(minutes=min(max(float(minutes), SOONEST), LATEST))
        self.store.set_wake(task.bot, next_at=at.isoformat(timespec="seconds"), next_why=" ".join(why.split())[:300])
        return at.astimezone()

    def news_for(self, bot: str, since: str) -> tuple[list[dict[str, Any]], list[Task], bool]:
        """What happened since `since` (UTC ISO) that's worth the bot's look: crew events that wake it, other than its
        own threads finishing (their results reach it anyway), and work that stopped, other than its own and what it
        delegated (which reports to it). And whether any of it is urgent."""
        mine = {t.id for t in self.store.list_tasks(bot=bot, limit=500)}
        events = [e for e in self.store.crew_events(since) if e["kind"] in WAKING
                  and not (e["kind"] == "thread_done" and e["subject"] in mine)]
        stopped = [t for t in self.store.list_tasks(limit=200) if t.updated_at >= since and t.status in STOPPED
                   and t.bot != bot and t.created_by != bot and t.kind not in ("review", "ask")]
        urgent = any(e["kind"] in URGENT for e in events) or any(t.status is Status.FAILED for t in stopped)
        return events, stopped, urgent

    def wake(self, bot: str, text: str) -> Task:
        """A look around: a turn in the bot's chat, prompted by crewbots, whose reply the user sees as said on its
        own. The next one is whenever it asks for, or news comes."""
        chat = self.chat(bot)
        self._waking.add(chat.id)
        self.store.set_wake(bot, looked_at=datetime.now(UTC).isoformat(timespec="milliseconds"), next_at=None,
                            next_why=None)
        return self.send(chat.id, text, sender="crewbots")
