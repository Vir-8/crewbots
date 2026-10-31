"""Work handed out in a group chat is talked about there: the handover, questions and answers between the bots doing it,
fix requests, and what each hands back are posted in the room by whoever said them, so its members and the user can
follow it and weigh in, whole. Each still reaches whoever it's for directly, so posting it wakes no one."""

from typing import Any

from crewbots.store import Handoff, Review, Status, Task

TOLD = (Status.IDLE, Status.FAILED, Status.CANCELLED)  # how a turn ends that's said in the room


class RoomTalk:
    """The part of Runner that says in a group chat what's said about the work handed out there."""

    def _work_room(self, task: Task) -> dict[str, Any] | None:
        """The group chat a piece of work was handed out in: that of the nearest seat at or above it, unless archived."""
        seat = next((t for t in self._chain(task) if t.kind == "seat" and t.room_id), None)
        if seat is None:
            return None
        try:
            room = self.store.room(seat.room_id)
        except KeyError:
            return None
        return None if room["archived_at"] else room

    def _say_in_room(self, task: Task, by: Task, to: str, text: str) -> None:
        """Says `text` in the group chat `task` was handed out in, from `by`."""
        if room := self._work_room(task):
            self._say(room, by, to, text)

    def _say(self, room: dict[str, Any], by: Task, to: str, text: str) -> None:
        """Says `text` to @`to` as the bot of `by`, if it's in the room. `to` hears it directly, so its seat there
        isn't sent it again, unless it has other news there to catch up on."""
        author = by.bot
        if author not in room["members"]:
            return
        seat = self.store.seat_of(room["id"], to)
        key = f"seat:{seat.id}" if seat else ""
        caught_up = seat and not any(m["author"] != to for m in self.store.room_messages(room["id"],
                                                                                         after=self.store.seen(key)))
        said = self.post(room["id"], author, text if to == author else f"@{to} {text}", recipients=[], by=by)
        if caught_up:
            self.store.mark_seen(key, said["id"])

    def _tell_room(self, task: Task) -> None:
        """A finished turn of work handed out in a group chat is said there by its bot, as soon as it ends, whatever
        its siblings are doing: whoever handed it out hears it once they've all finished."""
        if not task.parent_id or task.kind == "seat" or task.status not in TOLD:
            return
        parent = self.store.get_task(task.parent_id)
        key = f"told:{task.id}"
        if parent.kind == "review" or self.store.seen(key) > task.turns or not (room := self._work_room(task)):
            return
        self.store.mark_seen(key, task.turns + 1)
        self._say(room, task, parent.bot,
                  _said(task, self.store.latest_handoff(task.id), self.store.latest_review(task.id)))


def _said(task: Task, handoff: Handoff | None, review: Review | None) -> str:
    """A finished turn of work, as its bot says it in the group chat: what came of it, shortly."""
    if task.kind == "ask":
        return task.result if task.status is Status.IDLE and task.result else f"(no answer: {task.status})"
    if task.review_of and review:
        found = "".join(_finding(f) for f in review.findings)
        return (f"{task.id}, a review of {task.review_of}: {review.verdict.upper()}. {review.summary}"
                + (f"\n\nFindings:{found}" if found else ""))
    if task.status is Status.FAILED:
        return f"{task.id} failed: {task.error or 'no reason given'}"
    if handoff:
        return (f"{task.id}: {handoff.done}" + (f"\n\nStill open: {handoff.open}" if handoff.open.strip() else "")
                + ("\n\nEvidence: " + "; ".join(handoff.evidence) if handoff.evidence else ""))
    return f"{task.id}: {task.result or task.status.value}"


def _finding(finding: dict[str, str]) -> str:
    where = f"`{finding['location']}`: " if finding.get("location") else ""
    return f"\n- {where}{finding.get('problem', '')}" + (f" {finding['scenario']}" if finding.get("scenario") else "")
