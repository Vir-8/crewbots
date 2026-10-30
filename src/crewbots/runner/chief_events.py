"""Crew activity the chief should hear about without looking: threads done or failed, review verdicts and limits,
merges, escalations and stall alerts, for any bot but the chief itself (its own work reports to it already). They are
kept in the store and go to its main chat as one short notice, at most once every BATCH, or after QUIET when none of
them needs the chief, listing kinds, ids and bot names and a short title, with no message text, like the "State
changed" notice. Nothing happened, nothing sent. While the chat is stopped or waiting on the user the events just stay
in the store until a tick finds it idle."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from crewbots.runner.common import MergeError
from crewbots.store import Status, Task, Verdict

BATCH = timedelta(minutes=2)  # at most one notice this often
# What the chief may need to act on goes at the next BATCH. Routine news (work done, verdicts, merges) waits up to
# QUIET, gathering more, unless something that needs the chief comes first: each notice is a turn that reads the
# chief's whole conversation back, and most of this needs nothing from it.
NEEDS_CHIEF = {"failed", "escalation", "stall raised", "review limit", "merge failed"}
QUIET = timedelta(minutes=30)
LISTED = 20  # events a notice lists; the rest are counted
TITLE = 60  # characters of a task's title in a line
NOT_THREADS = ("chat", "seat", "ask", "review")

log = logging.getLogger(__name__)


class ChiefEvents:
    """The part of Runner that tells the chief what the crew is doing. It sits before the parts it wraps in Runner's
    bases, so each wrapper below runs first and calls the real method."""

    def _crew_event(self, kind: str, task_id: str, bot: str) -> None:
        """Keeps something for the next notice. The chief's own tasks are left out, and routine news of work it handed
        out, which reports to it already."""
        chief = self.chief_bot()
        if chief is None or bot == chief.name:
            return
        if kind not in NEEDS_CHIEF and self._handed_out_by(task_id) == chief.name:
            return
        self.store.add_chief_event(kind, task_id, bot)

    def _handed_out_by(self, task_id: str) -> str | None:
        try:
            task = self.store.get_task(task_id)
            return self.store.get_task(task.parent_id).bot if task.parent_id else None
        except KeyError:
            return None

    def waiting_on_user(self, task: Task) -> bool:
        """Whether the session is waiting on the user: not for the daemon to wake, and not a stall."""
        return task.status is Status.NEEDS_INPUT or self.store.has_pending_permission(task.id)

    def idle_chat(self, task: Task) -> bool:
        """Whether a notice can go to the session now without joining or interrupting anything: nothing running, in an
        idle flush, queued or waiting for it, status idle, no open escalation, not waiting on the user, not stopped."""
        return (task.id not in self._runs and not self._waiting.get(task.id) and task.status is Status.IDLE
                and task.id not in self._escalated and task.id not in self._cancelling
                and not self.waiting_on_user(task))

    def flush_chief_events(self, now: datetime | None = None) -> bool:
        """Sends the kept events as one notice, if there are any, BATCH has passed since the last, and the chief's main
        chat is completely idle (`idle_chat`). Otherwise they stay in the store for a later tick. They are
        cleared, and the time kept, only once the notice is in. True if sent."""
        now = now or datetime.now(UTC)
        events = self.store.chief_events()
        chief = self.chief_bot()
        if not events or chief is None:
            return False
        chat = self.chat(chief.name)
        if not self.idle_chat(chat):
            return False
        last = (self.store.routine_state("chief-events") or {}).get("last_run_at")
        if last and now - datetime.fromisoformat(last) < BATCH:
            return False
        if not any(e["kind"] in NEEDS_CHIEF for e in events) and now - datetime.fromisoformat(events[0]["at"]) < QUIET:
            return False  # routine news alone gathers for a while
        # a stall names several tasks in one subject: one entry each; the same thing twice is one line
        entries = [{**e, "subject": task} for e in events for task in (e["subject"].split(", ") if e["subject"] else [""])]
        latest = {(e["kind"], e["subject"] or e["who"]): e for e in entries}
        shown = [e for e in entries if latest[(e["kind"], e["subject"] or e["who"])] is e]
        lines = [self._event_line(e) for e in shown[:LISTED]]
        if len(shown) > LISTED:
            lines.append(f"- and {len(shown) - LISTED} more")
        try:
            self._state_notice(chief.name, "Crew activity since the last notice (ids only; ask for details):\n"
                               + "\n".join(lines))
        except Exception:
            log.exception("could not send the chief its crew activity notice")
            return False
        self.store.clear_chief_events(events[-1]["id"])
        self.store.routine_ran("chief-events", now.isoformat(), "")
        return True

    def _event_line(self, e: dict[str, Any]) -> str:
        if not e["subject"]:
            return f"- {e['kind']}: @{e['who']}"
        try:
            title = self.store.get_task(e["subject"]).title.strip()
        except KeyError:
            title = ""
        if len(title) > TITLE:
            head = title[:TITLE + 1]
            title = (head.rsplit(" ", 1)[0] if " " in head else title[:TITLE]).rstrip(" ,.;:-") + "…"
        return f"- {e['kind']}: {e['subject']} ({e['who']})" + (f" {title}" if title else "")

    def _report_up(self, task_id: str, now: bool = False) -> None:
        task = self.store.get_task(task_id)
        if (task.parent_id and not task.reported and not task.active and task_id not in self._runs
                and not self._waiting_for_editor(task_id) and task.kind not in NOT_THREADS and not task.review_of
                and task.status in (Status.IDLE, Status.FAILED)):
            latest = self.store.latest_handoff(task_id)
            seen = self.__dict__.setdefault("_chief_done", set())
            key = (task_id, task.turns, task.status, latest.id if latest else 0)
            if key not in seen:
                seen.add(key)
                self._crew_event("done" if task.status is Status.IDLE else "failed", task_id, task.bot)
        super()._report_up(task_id, now)

    def review(self, task_id: str, verdict: Verdict, summary: str, findings: list[dict[str, str]]):
        review = super().review(task_id, verdict, summary, findings)
        task = self.store.get_task(task_id)
        self._crew_event(f"review {verdict}", task.review_of, task.bot)
        if verdict is Verdict.FAIL and self.review_state(task.review_of).out_of_rounds:
            self._crew_event("review limit", task.review_of, self.store.get_task(task.review_of).bot)
        return review

    def merge(self, task_id: str, force: bool = False, *, automatic: bool = False) -> Task:
        bot = self.store.get_task(task_id).bot
        try:
            task = super().merge(task_id, force, automatic=automatic)
        except MergeError:
            self._crew_event("merge failed", task_id, bot)
            raise
        self._crew_event("merged", task_id, bot)
        return task

    def escalate(self, task_id: str, *args: Any, **kwargs: Any) -> None:
        super().escalate(task_id, *args, **kwargs)
        self._crew_event("escalation", task_id, self.store.get_task(task_id).bot)
