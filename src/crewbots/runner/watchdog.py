"""Work that must not be left waiting: a delegated task's late handoff reaches its delegator, and a bot with open work
that has gone quiet is woken twice, told the next step for each piece, then raised with the chief. For work handed out
in a group chat, both are said there, so its members see what's stuck. What it says carries only bots, ids, counts,
kinds and steps, never another bot's text."""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from crewbots import working_state
from crewbots.runner.reviews import MAX_REVIEW_ROUNDS
from crewbots.store import Status, Task

WAKES = 2  # reminders a bot gets, a quiet stall_minutes apart, before the chief hears of it

log = logging.getLogger(__name__)


def ids(work: list[str]) -> str:
    return ", ".join(dict.fromkeys(re.findall(r"\bt_[0-9a-f]+", " ".join(work))))


class Watchdog:
    """The part of Runner that makes sure results reach whoever is waiting on them."""

    def _report_late(self, task_id: str) -> None:
        """A handoff written while no turn of the task is running, such as in the turn Claude Code starts by itself
        when a background command finishes, reaches the delegator now, whatever its siblings are doing. `_report_up`
        otherwise runs only when a crewbots turn ends, and by then the task had already reported."""
        task = self.store.get_task(task_id)
        if not task.parent_id or task.active or task_id in self._runs:
            return
        if task.reported:
            self.store.update_task(task_id, reported=False)
        self._report_up(task_id, now=True)

    def stall_check(self, now: datetime | None = None) -> list[str]:
        """For each bot, and each session that owes it an action (the one that delegated work to it, or its main chat for its
        own): wake that session when it has been idle `stall_minutes` with work open, twice, and after another such wait
        put the group in the chief's Needs me. Groups are kept apart, so one that waits on the user or is stopped doesn't
        hold back another. Returns the bots woken or raised."""
        now = now or datetime.now(UTC)
        idle = timedelta(minutes=self.store.stall_minutes())
        acted: list[str] = []
        for name in self.crew.bots:
            try:
                groups = self._open_work(name)
                for key in [k for k in self._stalls if k[0] == name and k[1] not in groups]:
                    del self._stalls[key]  # nothing open there any more
                for target, work, key, touched in groups.values():
                    if self._stalled(name, target, work, key, touched, now, idle):
                        acted.append(name)
            except Exception:
                log.exception("stall check of %s failed", name)
        return acted

    def _stalled(self, name: str, target: Task, work: list[str], key: list[str], touched: list[str], now: datetime,
                 idle: timedelta) -> bool:
        state = self._stalls.get((name, target.id))
        if state is None or state["key"] != key:
            state = self._stalls[name, target.id] = {"key": key, "wakes": 0, "woke": None, "raised": False}
        if state["raised"] or self.store.chat_muted(name) or self._room_muted(target) or not self.idle_chat(target):
            return False  # running, queued, stopped, muted, or waiting on the user, which isn't a stall
        # quiet is measured on the session that owes the action and its open work, not on the bot's other sessions
        quiet_since = max(datetime.fromisoformat(t) for t in (target.updated_at, *touched, state["woke"]) if t)
        if now - quiet_since < idle:
            return False
        room = self.store.room(target.room_id) if target.kind == "seat" and target.room_id else None
        if state["wakes"] < WAKES:
            notice = ("Still open for you, with nothing run for a while: " + "; ".join(work)
                      + ". Pick it up, or say what is blocking it.")
            if room:
                self.post(room["id"], "crewbots", f"@{name} {notice}", recipients=[name])
            else:
                self._record(target.id, kind="state_notice", data={"message": notice})
                self.send(target.id, notice, sender="crewbots", _record_message=False)
            state["wakes"] += 1
            state["woke"] = now.isoformat()
            return True
        state["raised"] = True
        chief = self.coordinator()
        if chief is None or chief.name == name:
            return False  # the chief has no one above it to raise this with
        text = f"@{name} went quiet with open work after {WAKES} reminders: " + "; ".join(work)
        if room and chief.name in room["members"]:  # where the work is, for the chief to take up there
            self.post(room["id"], "crewbots", f"@{chief.name} {text}", recipients=[chief.name])
            return True
        try:
            working_state.edit(self.crew.memory.root / chief.name / "working-state" / "state.md", "Needs me", "add", text)
        except Exception:  # state full or unavailable: the chief still hears of it in its next notice
            log.exception("could not add the stall item for %s to the chief's state", name)
        self._crew_event("stall raised", ids(work), name)
        return True

    def _open_work(self, name: str) -> dict[str, tuple[Task, list[str], list[str], list[str]]]:
        """What is open for the bot, by the session that owes the action: lines of ids and counts, a key that changes
        when the work does, and when that work last changed, by that session's task id."""
        groups: dict[str, tuple[Task, list[str], list[str], list[str]]] = {}
        for t in self.store.owed_by_delegator(name) + self.store.asking_delegator(name):
            if not (step := self._next_step(t)):
                continue
            _, work, key, touched = groups.setdefault(t.parent_id, (self.store.get_task(t.parent_id), [], [], []))
            work.append(step)
            key.append(f"{t.id}:{step}")
            touched.append(t.updated_at)
        own: tuple[list[str], list[str], list[str]] = ([], [], [])
        for t in self.store.stuck_tasks(name):
            own[0].append(f"{t.id} is {t.status}")
            own[1].append(f"{t.id}:{t.status}")
            own[2].append(t.updated_at)
        path = self.crew.memory.root / name / "working-state" / "state.md"
        entries = (working_state.ENTRY.match(line) for line in working_state._sections(path)["Needs me"])
        needs = [m[2] for m in entries if m and m[1] == " "]
        if needs:
            own[0].append(f"{len(needs)} Needs-me item(s)")
            own[1].extend(needs)
        if own[0]:
            chat = self.chat(name)
            _, work, key, touched = groups.setdefault(chat.id, (chat, [], [], []))
            work.extend(own[0])
            key.extend(own[1])
            touched.extend(own[2])
        return groups

    def _next_step(self, work: Task) -> str | None:
        """What whoever handed `work` out should do with it next; None while it waits on someone else, or once it's out
        of review rounds, which the chief hears of (`review limit`) and the user decides."""
        who = f"{work.id} ({work.bot})"
        if work.status is Status.NEEDS_INPUT:
            return f"{who} asked you a question: answer it with message"
        if any(r.active or r.id in self._runs for r in self.store.reviewers_of(work.id)):
            return None
        state = self.review_state(work.id)
        if state.approved:
            return f"{who} passed every review: propose it with propose_merge"
        if not state.rounds or (state.rounds[-1] != state.version and not state.same_change):
            return f"{who} was handed back: have it reviewed (delegate with review_of), or drop it"
        if state.out_of_rounds:
            return None
        if state.failed:
            return (f"{who} failed review round {state.round} of {MAX_REVIEW_ROUNDS}: send back the findings you judge "
                    "real with message, or drop it")
        return f"{who} has a review that ended without a verdict: ask the reviewer with message, or review it again"

    def _room_muted(self, task: Task) -> bool:
        return bool(task.room_id and self.store.room(task.room_id)["muted"])

    @property
    def _stalls(self) -> dict[tuple[str, str], dict[str, Any]]:
        return self.__dict__.setdefault("_stall_state", {})
