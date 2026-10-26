"""What a bot does with its work: hands it back, asks a question, proposes it for merging (which only the user does), and
keeps notes."""

from pathlib import Path
from typing import Any

from crewbots import cards, prompts, worktree
from crewbots.memory import CREW_SCOPE, Note
from crewbots.routines import RoutineError
from crewbots.runner.common import MergeError, NotAllowed
from crewbots.runner.reviews import MAX_REVIEW_ROUNDS
from crewbots.sources import KINDS
from crewbots.store import Handoff, Task


class Work:
    """The part of Runner that takes a bot's handoffs, questions, merge proposals and notes."""

    def remember(self, task_id: str, title: str, body: str, shared: bool = False) -> Note:
        task = self.store.get_task(task_id)
        scope = CREW_SCOPE if shared else task.bot
        note = self.crew.memory.remember(scope, title, body, source=f"{task.id} ({task.bot})")
        self._record(task_id, kind="memory", data={"action": "remember", "scope": scope, "title": note.title,
                                                   "body": note.body, "path": str(note.path)})
        return note

    def forget(self, task_id: str, name: str, shared: bool = False) -> Note:
        task = self.store.get_task(task_id)
        scope = CREW_SCOPE if shared else task.bot
        note = self.crew.memory.forget(scope, name)
        self._record(task_id, kind="memory", data={"action": "forget", "scope": scope, "title": note.title})
        return note

    def recall(self, task_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """The best matches in the crew's record, one per task, leaving out the asking task's own conversation; then
        lines from the bot's and the crew's daily logs and notes."""
        hits, seen = [], set()
        for hit in self.store.search(query, limit=limit * 4, exclude_task=task_id):
            if hit["task_id"] not in seen:
                seen.add(hit["task_id"])
                hits.append(hit)
            if len(hits) == limit:
                break
        bot = self.store.get_task(task_id).bot
        hits += [{"task_id": "", "kind": m["kind"], "at": m["day"], "snippet": m["text"], "bot": m["scope"],
                  "title": m["path"]} for m in self.crew.memory.search([bot, CREW_SCOPE], query)]
        self.store.log_recall(task_id, bot, query, [h["task_id"] or h["title"] for h in hits])
        return hits

    def happened(self, kind: str, subject: str, text: str) -> dict[str, Any]:
        """Something happened around the crew (`sources.py`): it's kept, for check-ins to read, and each standing
        intent waiting for it fires, telling its bot in its chat."""
        event = self.store.add_crew_event(kind, subject, text)
        for intent in self.store.intents():
            if (intent["kind"] == kind and intent["bot"] in self.crew.bots and _mentions(intent["match"], f"{subject} {text}")
                    and self.store.intent_ready(intent)):
                self.store.fire_intent(intent["id"])
                self.say(intent["bot"], prompts.text("intent-fired", self.crew.home, then=intent["text"],
                                                     event=prompts.untrusted(kind, text), kind=kind, id=intent["id"]),
                         sender="crewbots")
        return event

    def remind_on(self, task_id: str, kind: str, match: str, then: str) -> dict[str, Any]:
        """The `remind_on` tool: a standing intent, which tells the bot `then` when an event of `kind` mentioning
        `match` happens: at most three times, a day apart, for 90 days."""
        if kind not in KINDS:
            raise NotAllowed(f"no event {kind!r}; the events are {', '.join(KINDS)}")
        if not then.strip():
            raise NotAllowed("say what to do or tell the user when it happens")
        return self.store.add_intent(self.store.get_task(task_id).bot, task_id, kind, match.strip(), then.strip())

    def propose_routine(self, task_id: str, name: str, description: str, goal: str, schedule: str | None = None,
                        on: str | None = None, repo: str | None = None) -> Path:
        """The `propose_routine` tool: a routine for the proposing bot, written off and marked as its proposal. Only
        the user turns it on (on the Routines page), and its first run is a test they see."""
        task = self.store.get_task(task_id)
        fields = {"bot": task.bot, "description": description, "schedule": schedule, "on": on, "repo": repo,
                  "proposed_by": task.bot}
        try:
            path = cards.save_routine(self.crew.home, name, fields, goal, create=True)
        except RoutineError as e:  # a schedule or trigger it couldn't run: the bot hears why
            raise NotAllowed(str(e)) from None
        self._record(task_id, kind="routine_proposed", data={"name": name, "when": schedule or f"on {on} in {repo}"})
        return path

    def update_state(self, task_id: str, state: str) -> Path:
        """The `update_state` tool: where this task stands, whole, for a fresh session of it to start from."""
        task = self.store.get_task(task_id)
        path = self.crew.memory.set_state(task.bot, task_id, state)
        self._record(task_id, kind="memory", data={"action": "state", "scope": task.bot, "title": "this task's state"})
        return path

    def _recent(self, bot: str) -> str | None:
        """The bot's and the crew's daily logs for the last two days, for a session to start with."""
        logs = [(whose, self.crew.memory.recent(scope)) for whose, scope in (("Yours", bot), ("The crew's", CREW_SCOPE))]
        return "\n\n".join(f"## {whose}\n\n{text}" for whose, text in logs if text) or None

    def propose_merge(self, parent_id: str, task_id: str, summary: str) -> Task:
        """A coordinator puts approved work in front of the user. Only the user merges."""
        work = self._own_child(parent_id, task_id)
        if not work.worktree_path:
            raise NotAllowed(f"{task_id} has no worktree, so there is nothing to merge")
        if work.merge_state == "merged":
            raise NotAllowed(f"{task_id} is already merged")
        state = self.review_state(task_id)
        if not state.approved:
            raise NotAllowed(
                f"{task_id} is not approved: every review of its current version must pass first "
                f"(round {state.round} of {MAX_REVIEW_ROUNDS}, version {state.version}, "
                f"reviewed versions {state.rounds or 'none'})")
        task = self._set(task_id, merge_state="proposed")
        self._record(task_id, kind="merge_proposed", data={"by": self.store.get_task(parent_id).bot, "summary": summary})
        return task

    def merge(self, task_id: str, force: bool = False) -> Task:
        """The user's merge: commits the worktree, merges its branch into the repo's current branch, removes the
        worktree. Refuses work that is still running or not approved (unless forced); a conflict changes nothing."""
        work = self.store.get_task(task_id)
        if not work.worktree_path:
            raise MergeError(f"{task_id} has no worktree")
        if work.merge_state == "merged":
            raise MergeError(f"{task_id} is already merged ({work.merged_commit})")
        if work.active or task_id in self._runs:
            raise MergeError(f"{task_id} is still working")
        state = self.review_state(task_id)
        if not state.approved and not force:
            raise MergeError(
                f"{task_id} is not approved (round {state.round}, reviewed versions {state.rounds or 'none'}, "
                f"current version {state.version}); pass force to merge anyway")
        wt = worktree.Worktree(repo=Path(work.repo), path=Path(work.worktree_path), branch=work.branch, base=work.base)
        reviewers = ", ".join(f"{t.bot} ({r.verdict if r else 'no verdict'})" for t, r in state.latest) or "none"
        try:
            worktree.commit(wt, f"{work.title}\n\ncrewbots task {work.id}, by {work.bot}")
            commit = worktree.merge(wt, f"Merge {work.branch}: {work.title}\n\nReviewed in round {state.round}: "
                                        f"{reviewers}.{' Merged without approval.' if not state.approved else ''}")
        except worktree.GitError as e:
            raise MergeError(str(e)) from None
        worktree.remove(wt, delete_branch=True)
        task = self._set(task_id, merge_state="merged", merged_commit=commit)
        self._record(task_id, kind="merged", data={"commit": commit, "into": str(wt.repo), "forced": not state.approved})
        return task

    def handoff(self, task_id: str, done: str, output: list[str], open: str, needs: str) -> Handoff:
        task = self.store.get_task(task_id)
        handoff = self.store.add_handoff(task_id, task.bot, done, output, open, needs)
        self._record(task_id, kind="handoff", data=handoff.to_json())
        self.crew.memory.log(task.bot, f"{task_id} {task.title}: {_short(done, 300)}"
                             + (f" Open: {_short(open, 200)}" if open.strip() else ""))
        return handoff

    def escalate(self, task_id: str, question: str) -> None:
        """Marks the task as waiting on a decision. Its parent hears at once; a top-level task waits for the user."""
        task = self.store.get_task(task_id)
        self._escalated.add(task_id)
        self._record(task_id, kind="escalation", data={"question": question})
        if task.parent_id:
            self.store.update_task(task_id, reported=True)
            self.send(task.parent_id, sender="crewbots", text=(
                f"{task.bot} escalated on task {task_id} ({task.title}):\n\n{question}\n\n"
                f"Answer with message(task_id={task_id!r}, text=...), or escalate it to the user if the decision "
                "is theirs."))


def _short(text: str, chars: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= chars else text[:chars - 1] + "…"


def _mentions(match: str, text: str) -> bool:
    """Whether `text` has every word of `match` (any text does, for no match)."""
    return all(word in text.lower() for word in match.lower().split())
