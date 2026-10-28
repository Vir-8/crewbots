"""What a bot does with its work: hands it back, asks a question, proposes it for merging, and
keeps notes."""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from crewbots import cards, pages, prompts, worktree
from crewbots.memory import CREW_SCOPE, MemoryError, Note
from crewbots.pages import Page
from crewbots.routines import RoutineError
from crewbots.runner.common import MergeError, NotAllowed
from crewbots.runner.reviews import MAX_REVIEW_ROUNDS
from crewbots.sources import KINDS
from crewbots.store import Handoff, Status, Task

CHOICES = 6  # offered with a question, at most
LABEL_CHARS, DESCRIPTION_CHARS = 80, 240  # of a choice
OTHER = {"other", "something else", "other (specify)", "other (please specify)"}  # the app always offers Other


class Work:
    """The part of Runner that takes a bot's handoffs, questions, merge proposals and notes."""

    def remember(self, task_id: str, title: str, body: str, shared: bool = False) -> Note:
        task = self.store.get_task(task_id)
        scope = CREW_SCOPE if shared else task.bot
        note = self.crew.memory.remember(scope, title, body, source=f"{task.id} ({task.bot})")
        self._record(task_id, kind="memory", data={"action": "remember", "scope": scope, "title": note.title,
                                                   "body": note.body, "path": str(note.path)})
        return note

    def write_page(self, task_id: str, name: str, text: str, find: str | None = None) -> Page:
        """One of the bot's pages, written whole, or with `find` replaced by `text`."""
        task = self.store.get_task(task_id)
        folder = self.crew.pages_of(self.crew.bots[task.bot])
        page = pages.edit(folder, name, find, text) if find is not None else pages.write(folder, name, text)
        self.bus.publish({"type": "page", "bot": task.bot, "page": page.name})
        return page

    def remember_for(self, bot: str, text: str) -> Note:
        """A note the user gives a bot (`/remember`): its first line is the title. It's in the bot's chat as remembered,
        and reaches its session with its other notes."""
        lines = text.strip().splitlines() or [""]
        title = lines[0].strip()[:80]
        note = self.crew.memory.remember(bot, title, text.strip(), source="the user")
        self._record(self.chat(bot).id, kind="memory", data={"action": "remember", "scope": bot, "title": note.title,
                                                           "body": note.body, "path": str(note.path), "by": "user"})
        return note

    def thread_for(self, bot: str, goal: str) -> Task:
        """A thread the user starts from a bot's chat (`/new`): under the chat, where its result comes back."""
        chat = self.chat(bot)
        return self.submit(bot, goal, parent_id=chat.id, workspace=chat.workspace)

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
        """A coordinator proposes approved work, merging it locally if the user enabled that."""
        parent = self.store.get_task(parent_id)
        if parent.bot not in self.crew.bots or not self.crew.bots[parent.bot].can_delegate:
            raise NotAllowed("only a coordinator can propose a merge")
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
        self._record(task_id, kind="merge_proposed", data={"by": parent.bot, "summary": summary})
        if self.store.auto_merge():
            try:
                return self.merge(task_id, automatic=True)
            except MergeError as e:
                raise MergeError(f"Automatic merge failed; the proposal is waiting for the user: {e}") from None
        return task

    def merge(self, task_id: str, force: bool = False, *, automatic: bool = False) -> Task:
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
        proposals = self.store.last_events(task_id, ("merge_proposed",), 1)
        summary = proposals[0].data.get("summary", "") if proposals else ""
        subject = _merge_subject(summary, work.title)
        try:
            worktree.commit(wt, f"{subject}\n\ncrewbots task {work.id}, by {work.bot}")
            commit = worktree.merge(wt, f"{subject}\n\nReviewed in round {state.round}: "
                                        f"{reviewers}.{' Merged without approval.' if not state.approved else ''}")
        except worktree.GitError as e:
            raise MergeError(str(e)) from None
        task = self._set(task_id, merge_state="merged", merged_commit=commit)
        self._record(task_id, kind="merged", data={"commit": commit, "into": str(wt.repo), "forced": not state.approved,
                                                   "automatic": automatic})
        try:
            worktree.remove(wt, delete_branch=True)
        except worktree.GitError as e:
            self._record(task_id, kind="merge_cleanup_failed", data={"reason": str(e)})
        return task

    def withdraw_merge(self, task_id: str, reason: str, *, parent_id: str | None = None) -> Task:
        """Withdraw a proposal without changing its worktree."""
        if parent_id is not None:
            parent = self.store.get_task(parent_id)
            if parent.bot not in self.crew.bots or not self.crew.bots[parent.bot].can_delegate:
                raise NotAllowed("only a coordinator can withdraw a merge")
            self._own_child(parent_id, task_id)
            by = parent.bot
        else:
            by = "user"
        if not reason.strip():
            raise NotAllowed("give a reason for withdrawing the proposal")
        try:
            task = self.store.get_task(task_id)
        except KeyError:
            raise NotAllowed(f"no task {task_id}") from None
        if task.merge_state != "proposed":
            raise NotAllowed(f"{task_id} has no merge proposal to withdraw")
        task = self._set(task_id, merge_state=None)
        self._record(task_id, kind="merge_withdrawn", data={"by": by, "reason": reason.strip()})
        return task

    def handoff(self, task_id: str, done: str, output: list[str], open: str, needs: str) -> Handoff:
        task = self.store.get_task(task_id)
        handoff = self.store.add_handoff(task_id, task.bot, done, output, open, needs)
        self._record(task_id, kind="handoff", data=handoff.to_json())
        self._close_questions(task_id)
        self._refresh_working_state(task.bot)
        self.crew.memory.log(task.bot, f"{task_id} {task.title}: {_short(done, 300)}"
                             + (f" Open: {_short(open, 200)}" if open.strip() else ""))
        return handoff

    def _answer_decision(self, task: Task, text: str) -> tuple[bool, bool]:
        """Record only selected choices or an answer reference, and nudge the session for free text."""
        if task.status in (Status.FAILED, Status.CANCELLED):
            return False, False
        event = self.store.last_event(task.id, ("escalation",))
        resolved = event and self.store.db.execute(
            "select 1 from events where task_id = ? and id > ? and "
            "(kind = 'questions_closed' or (kind = 'message' and json_extract(data, '$.from') = 'user')) limit 1",
            (task.id, event.id)).fetchone()
        if not event or resolved:
            return False, False
        question = " ".join(event.data.get("question", "").split())[:160]
        choices = {str(c.get("label", "")) for c in event.data.get("choices", []) if isinstance(c, dict)}
        # The app sends a single label, or (for multiple choice) one "- label" per line.
        # It sends Other through the same message API, so only a complete match is safe to share.
        selected = [text] if text in choices else []
        if not selected and event.data.get("multiple"):
            lines = text.split("\n")
            if all(line.startswith("- ") and line[2:] in choices for line in lines):
                selected = [line[2:] for line in lines]
        nudge = not selected
        if selected:
            answer = ", ".join(selected)
        else:
            where = "room" if task.kind == "seat" else "chat" if task.kind == "chat" else "thread"
            answer = f"answered in {where} on {datetime.now().astimezone().date()} (task {task.id})"
        try:
            from crewbots.working_state import edit

            edit(self.crew.memory.root / task.bot / "working-state" / "state.md", "Decisions", "add",
                 f"{event.at[:10]} {task.id}: {question} — {answer}", automatic=True)
        except (MemoryError, OSError) as e:
            self._record(task.id, kind="notice", data={"message": f"Could not record decision: {e}"})
        return True, nudge

    def escalate(self, task_id: str, question: str, choices: list[Any] | None = None, multiple: bool = False) -> None:
        """Marks the task as waiting on an answer: a decision that isn't its bot's, or what's wanted when it's unsure.
        With `choices`, the user picks one (or several, if `multiple`), or answers in their own words. Its parent
        hears at once; a top-level task waits for the user."""
        task = self.store.get_task(task_id)
        offered = _choices(choices)
        self._escalated.add(task_id)
        self._record(task_id, kind="escalation", data={"question": question} | (
            {"choices": offered, "multiple": bool(multiple)} if offered else {}))
        self._refresh_working_state(task.bot)
        if task.kind == "seat" and task.room_id and (chief := self.coordinator()) and task.bot != chief.name:
            self._chief_room_item(self.store.room(task.room_id), task.bot, "escalated", f"task {task.id}")
        if task.parent_id:
            parent = self.store.get_task(task.parent_id)
            chief = self.coordinator()
            if task.kind != "seat" and parent.kind == "seat" and parent.room_id and chief and parent.bot == chief.name:
                self._chief_room_item(self.store.room(parent.room_id), task.bot, "escalated", f"task {task.id}")
            listed = "".join(f"\n- {c['label']}" + (f": {c['description']}" if "description" in c else "") for c in offered)
            self.store.update_task(task_id, reported=True)
            self.send(task.parent_id, sender="crewbots", text=(
                f"{task.bot} escalated on task {task_id} ({task.title}):\n\n{question}\n\n"
                + (f"Its choices{' (any of them)' if multiple else ''}:{listed}\n\n" if offered else "")
                + f"Answer with message(task_id={task_id!r}, text=...), or escalate it to the user if the decision "
                "is theirs."))


def _choices(given: list[Any] | None) -> list[dict[str, str]]:
    """A question's choices as {label, description}: from labels, or labels with descriptions, without repeats and
    without an "other" (the app always offers one)."""
    out: list[dict[str, str]] = []
    for item in given or []:
        label, description = (item.get("label"), item.get("description")) if isinstance(item, dict) else (item, None)
        label = _short(str(label or ""), LABEL_CHARS)
        if not label or label.lower().rstrip(".:…") in OTHER or any(c["label"].lower() == label.lower() for c in out):
            continue
        choice = {"label": label}
        if description and (description := _short(str(description), DESCRIPTION_CHARS)):
            choice["description"] = description
        out.append(choice)
    return out[:CHOICES]


def _short(text: str, chars: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= chars else text[:chars - 1] + "…"


def _merge_subject(summary: str, title: str, limit: int = 72) -> str:
    paragraph = " ".join(re.split(r"\n\s*\n", summary.strip(), maxsplit=1)[0].split())
    abbreviation = r"\b(?:e\.g|i\.e|etc|mr|mrs|ms|dr|vs|inc)\.$"
    if paragraph and len(paragraph) <= limit and not re.fullmatch(abbreviation, paragraph, re.I):
        return paragraph
    for end in re.finditer(r"[.!?](?=\s|$)", paragraph):
        sentence = paragraph[:end.end()]
        if len(sentence) > limit:
            break
        if len(sentence) >= 20 and not re.search(abbreviation, sentence, re.I):
            return sentence
    fallback = " ".join(title.split()) or "Merge work"
    if len(fallback) <= limit:
        return fallback
    prefix = fallback[:limit - 1]
    if not prefix[-1].isspace() and not fallback[limit - 1].isspace() and " " in prefix:
        prefix = prefix.rsplit(" ", 1)[0]
    return prefix.rstrip() + "…"


def _mentions(match: str, text: str) -> bool:
    """Whether `text` has every word of `match` (any text does, for no match)."""
    return all(word in text.lower() for word in match.lower().split())
