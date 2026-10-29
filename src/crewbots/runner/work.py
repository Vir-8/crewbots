"""What a bot does with its work: hands it back, asks a question, proposes it for merging, and keeps notes."""

import asyncio
import logging
import os
import re
import secrets
import tempfile
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from crewbots import branch_mode, cards, pages, prompts, timefmt, worktree
from crewbots.crew import NO_REPLY
from crewbots.events import Notice
from crewbots.memory import CREW_SCOPE, MemoryError, Note
from crewbots.pages import Page
from crewbots.routines import RoutineError
from crewbots.runner.checks import MAX_CHECKS
from crewbots.runner.closing import Closing
from crewbots.runner.common import MergeError, NotAllowed
from crewbots.runner.landed import Landed
from crewbots.runner.merge_message import merge_message
from crewbots.runner.merge_status import MergeStatus
from crewbots.runner.reviews import MAX_REVIEW_ROUNDS
from crewbots.sources import KINDS
from crewbots.store import Handoff, Status, Task

CHOICES = 6  # offered with a question, at most
LABEL_CHARS, DESCRIPTION_CHARS = 80, 240  # of a choice
OTHER = {"other", "something else", "other (specify)", "other (please specify)"}  # the app always offers Other
DROPPED = re.compile(r"\s*dropped\s*:\s*(.*)", re.IGNORECASE | re.DOTALL)  # a handoff that closes its work on purpose
log = logging.getLogger(__name__)


class Work(Landed, Closing, MergeStatus):
    """The part of Runner that takes a bot's handoffs, questions, merge proposals and notes."""

    def _check_worktree_sweep(self, task_id: str | None = None) -> None:
        if not self._worktree_sweep_done.is_set():
            raise NotAllowed("Worktree maintenance is running; try again when it finishes.")
        self.check_not_merging(task_id)  # the task's own merge, for withdraw and drop

    def changes(self, task_id: str) -> str | None:
        """The task's diff against where its worktree started, or None if it has no worktree."""
        task = self.store.get_task(task_id)
        self._check_worktree_sweep()
        if not task.worktree_path:
            if task.base_branch and task.branch and task.repo:
                return worktree._git(Path(task.repo), "diff", task.base, task.branch, strip=False)
            return None
        if task.base_branch and task.merge_state != "merged":
            checkout = self._branch_checkout(task)
            if not branch_mode.on_branch(checkout):
                raise worktree.GitError("HEAD is not on the task branch")
            with branch_mode.snapshot(checkout) as (git, _):
                return git("diff", "--cached", task.base)
        wt = worktree.Worktree(repo=Path(task.repo), path=Path(task.worktree_path), branch=task.branch, base=task.base)
        if task.merge_state == "merged":
            return worktree._git(wt.repo, "diff", wt.base, self._merged_tip(task), strip=False)
        if not wt.path.exists():
            return worktree._git(wt.repo, "diff", wt.base, wt.branch, strip=False)
        return worktree.changes(wt)

    def _branch_checkout(self, task: Task) -> branch_mode.BranchCheckout:
        return branch_mode.BranchCheckout(Path(task.repo), task.branch, task.base_branch, task.base)

    def _merged_tip(self, task: Task) -> str:
        if task.merged_tip:
            return task.merged_tip
        # Older merge records have no tip. A merge commit's second parent is the original task tip.
        return worktree._git(Path(task.repo), "rev-parse", "--verify", f"{task.merged_commit}^2")

    def _restore_worktree(self, task: Task) -> None:
        wt = worktree.Worktree(Path(task.repo), Path(task.worktree_path), task.branch, task.base)
        if not wt.path.exists():
            tip = self._merged_tip(task) if task.merge_state == "merged" else None
            worktree.restore(wt, tip=tip)

    def _setting(self, task: Task) -> str | None:
        if task.kind == "chat":
            return prompts.text("chat-setting", self.crew.home)
        if task.created_by == "crewbots" and task.parent_id:  # work it started on its own, from a check-in
            return prompts.text("own-setting", self.crew.home)
        if task.kind == "ask":
            return prompts.text("ask-setting", self.crew.home, asker=task.created_by)
        if task.kind == "seat" and task.room_id:
            room = self.store.room(task.room_id)
            others = [m for m in room["members"] if m != task.bot]
            lead = (f"You lead it: a message from the user that mentions no one comes to you. Answer it, hand it to the "
                    f"one member best placed for it with a short handover that @mentions them, or reply {NO_REPLY} if "
                    f"someone already has it." if room["lead"] == task.bot
                    else f"{room['lead']} leads it; you're woken when someone mentions you.")
            setting = prompts.text("seat-setting", self.crew.home, title=repr(escape(room["title"], quote=False)),
                                   lead=lead, no_reply=NO_REPLY, others=", ".join(others) or "no other bots")
            about = self.room_about(room["id"])
            return f"{setting}\n\nWhat this group chat is for: {about}" if about else setting
        return None

    def _clear_room_need(self, task_id: str) -> None:
        import logging

        from crewbots import working_state

        chief = self.coordinator()
        if chief:
            try:
                working_state.clear_need(self.crew.memory.root / chief.name / "working-state" / "state.md", task_id)
            except Exception:
                logging.getLogger(__name__).exception("Could not clear a resolved room escalation")

    def _close_questions(self, task_id: str) -> None:
        asked = self.store.last_event(task_id, ("escalation", "permission"))
        closed = self.store.last_event(task_id, ("questions_closed",))
        if asked and (closed is None or asked.id > closed.id):
            self._record(task_id, kind="questions_closed", data={})
        self._close_seat_questions(task_id)
        self._clear_room_need(task_id)

    def _begin_cancel(self, task_id: str) -> None:
        self.store.get_task(task_id)
        if task_id in self._cancelling:
            raise NotAllowed("This task is already being cancelled.")
        self._cancelling.add(task_id)
        try:
            if (floor := self._seat_queue_floor.pop(task_id, None)) is not None:
                self.store.rewind_seat_seen(task_id, floor)
            if run := self._runs.get(task_id):
                run.cancel()
            self._waiting.pop(task_id, None)
            self._owed.discard(task_id)
            self._nudged.discard(task_id)
            self._escalated.discard(task_id)
            self.store.clear_pending(task_id)
            self._set(task_id, status=Status.CANCELLED)  # close questions before cleanup can yield
        except BaseException:
            self._cancelling.discard(task_id)
            raise

    def remember(self, task_id: str, title: str, body: str, shared: bool = False, mistake: str = "",
                 check: str = "") -> Note:
        """A note for next time. With `mistake`, it's the lesson of a correction, which is counted too: the weekly
        retro looks for mistakes that came back and turns them into checks (`check`: one that would have caught it)."""
        task = self.store.get_task(task_id)
        scope = CREW_SCOPE if shared else task.bot
        note = self.crew.memory.remember(scope, title, body, source=f"{task.id} ({task.bot})")
        self._record(task_id, kind="memory", data={"action": "remember", "scope": scope, "title": note.title,
                                                   "body": note.body, "path": str(note.path)})
        if mistake.strip():
            self._record(task_id, kind="correction", data={"bot": task.bot, "title": note.title, "mistake": mistake.strip(),
                                                           "lesson": note.body, "check": check.strip()})
        return note

    def corrections_of(self, bot: str, limit: int = 5) -> list[str]:
        """The titles of a bot's latest corrections, the latest first."""
        noted = self.store.recent_events(limit=200, kinds=("correction",))
        return [e.data["title"] for e in reversed(noted) if e.data.get("bot") == bot][:limit]

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
        """The best matches in the crew's record, one per task; then lines from the bot's and the crew's daily logs and
        notes. A thread's own conversation is left out, since it has it all; a chat or group chat is compacted as it
        goes, so what was folded away is found here again."""
        hits, seen = [], set()
        own = self.store.get_task(task_id).kind in ("chat", "seat")
        for hit in self.store.search(query, limit=limit * 4, exclude_task=None if own else task_id):
            if hit["task_id"] == task_id or hit["task_id"] not in seen:
                seen.add(hit["task_id"])
                hits.append(hit)
            if len(hits) == limit:
                break
        bot = self.store.get_task(task_id).bot
        hits += [{"task_id": "", "kind": m["kind"], "at": m["day"], "snippet": m["text"], "bot": m["scope"],
                  "title": m["path"]} for m in self.crew.memory.search([bot, CREW_SCOPE], query)]
        self.store.log_recall(task_id, bot, query, [h["task_id"] or h["title"] for h in hits])
        return [h | {"at": timefmt.stamp(h["at"])} if h.get("at") else h for h in hits]

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
        if not self.is_coordinator(parent.bot):
            raise NotAllowed("only a coordinator can propose a merge")
        work = self._own_child(parent_id, task_id)
        if not work.worktree_path:
            raise NotAllowed(f"{task_id} has no worktree, so there is nothing to merge")
        if work.merge_state == "merged":
            raise NotAllowed(f"{task_id} is already merged")
        if work.merge_state == "merging":
            raise NotAllowed(f"{task_id} has a merge awaiting recovery")
        if wrong := self.wrong_branch(work):
            raise NotAllowed(f"{task_id} cannot be proposed: {wrong}")
        state = self.review_state(task_id)
        if not state.approved:
            raise NotAllowed(
                f"{task_id} is not approved: every review of its current version must pass first "
                f"(round {state.round} of {MAX_REVIEW_ROUNDS}, version {state.version}, "
                f"reviewed versions {state.rounds or 'none'})")
        try:
            files = branch_mode.changed_files(self._branch_checkout(work)) if work.base_branch else None
        except worktree.GitError as e:
            raise NotAllowed(str(e)) from None
        auto = self.store.auto_merge()  # the app asks the user to merge only when it isn't going to happen by itself
        task = self._set(task_id, merge_state="proposed")
        self._record(task_id, kind="merge_proposed", data={"by": parent.bot, "summary": summary, "auto": auto,
                                                          **({"files": files} if files is not None else {})})
        if auto:
            try:
                return self.merge(task_id, automatic=True)
            except MergeError as e:
                self._record(task_id, kind="merge_failed", data={"error": str(e), "automatic": True})
                raise MergeError(f"Automatic merge failed; the proposal is waiting for the user: {e}") from None
        return task

    def merge(self, task_id: str, force: bool = False, *, automatic: bool = False) -> Task:
        """The user's merge: commits the worktree, merges its branch into the repo's current branch, removes the
        worktree. Refuses work that is still running or not approved (unless forced); a conflict changes nothing."""
        self._check_worktree_sweep()
        work = self.store.get_task(task_id)
        if not work.worktree_path:
            raise MergeError(f"{task_id} has no worktree")
        if work.merge_state == "merged":
            raise MergeError(f"{task_id} is already merged ({work.merged_commit})")
        if work.merge_state == "merging":
            raise MergeError(f"{task_id} has a merge awaiting recovery")
        if work.active or task_id in self._runs:
            raise MergeError(f"{task_id} is still working")
        state = self.review_state(task_id)
        if not state.approved and not force:
            raise MergeError(
                f"{task_id} is not approved (round {state.round}, reviewed versions {state.rounds or 'none'}, "
                f"current version {state.version}); pass force to merge anyway")
        if into := self.already_on(work):
            raise MergeError(f"{task_id} is already on {into}: its changes are all there, so there is nothing to merge; "
                             "withdraw the proposal instead")
        wt = worktree.Worktree(repo=Path(work.repo), path=Path(work.worktree_path), branch=work.branch, base=work.base)
        proposals = self.store.last_events(task_id, ("merge_proposed",), 1)
        last = proposals[0].data if proposals else {}
        message = merge_message(last.get("summary", ""), work.title, unreviewed=not state.approved)
        try:
            if work.base_branch:
                def mark_merging(commit: str, tip: str) -> str:
                    self.ci_gate(work, work.base_branch, commit=commit)
                    marker = f"{time.time_ns()}:{secrets.token_hex(16)}"
                    self._set(task_id, merge_state="merging", merged_commit=commit, merged_tip=tip,
                              merge_lock=None, merge_marker=marker)
                    return marker

                commit, tip = branch_mode.merge(self._branch_checkout(work), message, expected_files=last.get("files"),
                                                before_lock=mark_merging)
                try:
                    leftovers, check_error = branch_mode.leftover_files(wt.repo), None
                except (worktree.GitError, OSError) as e:
                    leftovers, check_error = None, str(e)
                    log.exception("Merged %s but could not check for later saves", task_id)
                task = self._set(task_id, merge_state="merged", merged_commit=commit, merged_tip=tip,
                                 merge_lock=None, merge_marker=self._marker_to_keep(task_id))
                self._record(task_id, kind="merged", data={"commit": commit, "into": str(wt.repo),
                                                           "forced": not state.approved, "automatic": automatic,
                                                           "leftover_files": leftovers, "leftover_check_error": check_error})
                return task
            if not wt.path.exists():
                self._restore_worktree(work)
            worktree.commit(wt, f"{message.splitlines()[0]}\n\ncrewbots task {work.id}, by {work.bot}")
            tip, commit = self.merge_gated(work, wt, message)
        except worktree.GitError as e:
            current = self.store.get_task(task_id)
            if branch_mode.LOCK_LEFT in str(e) or branch_mode.LOCK_STUCK in str(e):
                self._record(task_id, Notice(message=f"Merge failed: {e}"))
            if current.merge_state == "merging":
                try:
                    base_tip = worktree._git(Path(current.repo), "rev-parse", f"refs/heads/{current.base_branch}")
                    if branch_mode.is_ancestor(Path(current.repo), current.merged_commit, base_tip):
                        # The ref transaction landed: refs and HEAD stay as found, as in startup recovery.
                        self._set(task_id, merge_state="merged", merge_lock=None,
                                  merge_marker=self._marker_to_keep(task_id))
                        self._record(task_id, kind="merged", data={
                            "commit": current.merged_commit, "into": current.repo, "forced": not state.approved,
                            "automatic": automatic, "leftover_files": None,
                            "leftover_check_error": f"Merge step failed after the base advanced: {e}; "
                                                    "refs and checkout left as found"})
                        raise MergeError(f"{e}; the merge already reached {current.base_branch}, "
                                         "so the task is marked merged") from None
                    self._set(task_id, merge_state="proposed", merged_commit=None, merged_tip=None,
                              merge_lock=None, merge_marker=self._marker_to_keep(task_id))
                except worktree.GitError:
                    log.exception("Could not determine merge outcome for %s", task_id)
            raise MergeError(str(e)) from None
        task = self._set(task_id, merge_state="merged", merged_commit=commit, merged_tip=tip)
        self._record(task_id, kind="merged", data={"commit": commit, "into": str(wt.repo), "forced": not state.approved,
                                                   "automatic": automatic})
        self._cleanup_after_action(task)
        return task

    def _cleanup_after_action(self, task: Task) -> None:
        try:
            result = self._cleanup_worktree(task)
            if result["status"] != "removed":
                log.warning("Worktree cleanup for %s: %s", task.id, result.get("reason"))
        except Exception:
            log.exception("Could not clean up worktree for %s", task.id)

    def _cleanup_worktree(self, task: Task, *, sweep: bool = False, dry_run: bool = False) -> dict[str, str]:
        result = {"task_id": task.id, "path": task.worktree_path or "", "branch": task.branch or ""}
        if task.base_branch:
            return result | {"status": "kept", "reason": "branch-mode project checkout"}
        busy = {Status.QUEUED, Status.RUNNING, Status.NEEDS_INPUT, Status.HELD, Status.INTERRUPTED}
        if (task.status in busy or task.id in self._runs or task.merge_state == "proposed" or any(
                r.status in busy or r.id in self._runs for r in self.store.reviewers_of(task.id))):
            return result | {"status": "kept", "reason": "task or reviewer is active, or proposal is open"}
        if not all((task.repo, task.worktree_path, task.branch, task.base)):
            return result | {"status": "kept", "reason": "incomplete worktree metadata"}
        wt = worktree.Worktree(Path(task.repo), Path(task.worktree_path), task.branch, task.base)
        original_head = None
        saved_head = None
        index_path = None
        index_before = None
        removing = False
        try:
            if not wt.path.is_dir():
                return result | {"status": "kept", "reason": "worktree path is missing"}
            if wt.repo.resolve() == wt.path.resolve():
                return result | {"status": "kept", "reason": "path is the main checkout"}
            try:
                head = worktree._git(wt.path, "symbolic-ref", "--short", "HEAD")
            except worktree.GitError:
                head = None
            if head != wt.branch:  # the work was done on a branch of its own naming; the branch is kept either way
                into = self.foreign_head_removable(task, wt, head)
                if dry_run:
                    return result | {"status": "would_remove", "branch_status": "kept",
                                     "reason": f"on {head}, whose work is already on {into}; branch kept"}
                worktree.remove(wt)
                self._removed_worktrees.add(task.id)
                log.info("Removed worktree for %s; its branch %s is kept", task.id, head)
                return result | {"status": "removed", "branch_status": "kept"}
            untracked = worktree._git(wt.path, "ls-files", "--others", "--exclude-standard", "-z", strip=False)
            staged = worktree._git(wt.path, "ls-files", "--stage", "-z", strip=False)
            gitlinks = [entry.split("\t", 1)[1] for entry in staged.split("\0")
                        if entry.startswith("160000 ") and "\t" in entry]
            if any((wt.path / name / ".git").exists() for name in [*untracked.split("\0"), *gitlinks] if name):
                raise worktree.GitError("worktree contains an untracked nested repository; leaving it in place")
            if dry_run:
                notes = []
                # --no-optional-locks is GIT_OPTIONAL_LOCKS=0: a dry run must not rewrite the index.
                if worktree._git(wt.path, "--no-optional-locks", "status", "--porcelain", "--ignore-submodules=none"):
                    notes.append("uncommitted changes would be saved to the branch first")
                ahead = worktree._git(wt.repo, "rev-list", "--count", f"{wt.base}..{wt.branch}")
                if ahead != "0" and task.merge_state != "merged":
                    notes.append(f"{ahead} unmerged commit(s) stay on the branch")
                later = "0"
                if task.merge_state == "merged" and task.merged_tip:
                    later = worktree._git(wt.repo, "rev-list", "--count", f"{task.merged_tip}..{wt.branch}")
                    if later != "0":
                        notes.append(f"merged, {later} later commit(s) kept on the branch")
                if not notes:
                    notes.append("merged and clean" if task.merge_state == "merged" else "clean, nothing unmerged")
                return result | {"status": "would_remove", "branch_status": "kept", "reason": "; ".join(notes)}
            if task.merge_state == "merged" and not sweep:
                if not task.merged_commit:
                    raise worktree.GitError("merged task has no recorded merge commit")
            else:
                original_head = worktree._git(wt.path, "rev-parse", "HEAD")
                index_path = Path(worktree._git(wt.path, "rev-parse", "--git-path", "index"))
                if not index_path.is_absolute():
                    index_path = wt.path / index_path
                index_before = index_path.read_bytes() if index_path.exists() else None
                saved_head = worktree.commit(wt, f"Uncommitted work from {task.id}, saved on removal")
            if worktree._git(wt.path, "status", "--porcelain", "--ignore-submodules=none"):
                raise worktree.GitError("worktree still has changes; leaving it in place")
            branch_tip = worktree._git(wt.repo, "rev-parse", f"refs/heads/{wt.branch}")
            if worktree._git(wt.path, "rev-parse", "HEAD") != branch_tip or (saved_head and saved_head != branch_tip):
                raise worktree.GitError("task branch changed while saving; leaving the worktree in place")
            delete_branch = task.merge_state == "merged" and not sweep
            if delete_branch:
                worktree.branch_merged(wt.repo, branch_tip, task.merged_tip, task.merged_commit)
            removing = True
            deleted = worktree.remove(wt, delete_branch=delete_branch, branch_tip=branch_tip)
        except (worktree.GitError, OSError) as e:
            # Once removal was attempted it may have partially removed files. Keep the saved commit so that
            # tracked and non-ignored untracked work remains recoverable from the branch.
            if not removing and index_path is not None and wt.path.is_dir():
                try:
                    if saved_head:
                        worktree._git(wt.path, "update-ref", f"refs/heads/{wt.branch}", original_head, saved_head)
                    index_path = Path(worktree._git(wt.path, "rev-parse", "--git-path", "index"))
                    if not index_path.is_absolute():
                        index_path = wt.path / index_path
                    if index_before is None:
                        index_path.unlink(missing_ok=True)
                    else:
                        with tempfile.NamedTemporaryFile(dir=index_path.parent, delete=False) as temp:
                            temp.write(index_before)
                            temporary = temp.name
                        os.replace(temporary, index_path)
                except (worktree.GitError, OSError):
                    log.exception("Could not roll back the worktree save for %s", task.id)
            log.exception("Could not clean up worktree for %s", task.id)
            return result | {"status": "kept" if wt.path.exists() else "error", "reason": str(e)}
        self._removed_worktrees.add(task.id)
        self._record(task.id, kind="worktree_dropped", data={"path": result["path"]})
        log.info("Removed worktree for %s; branch %s", task.id, "deleted" if deleted else "kept")
        return result | {"status": "removed", "branch_status": "deleted" if deleted else "kept"}

    def sweep_worktrees(self, dry_run: bool = False) -> dict[str, list[dict[str, str]]]:
        rows = self.store.db.execute("select id from tasks where worktree_path is not null order by id").fetchall()
        results = [self._cleanup_worktree(self.store.get_task(row[0]), sweep=True, dry_run=dry_run) for row in rows]
        gone = "would_remove" if dry_run else "removed"
        report = {gone: [r for r in results if r["status"] == gone],
                  "kept": [r for r in results if r["status"] != gone]}
        log.info("Worktree sweep%s: %s", " (dry run)" if dry_run else "", report)
        return report

    async def run_worktree_sweep(self, dry_run: bool = False) -> dict[str, list[dict[str, str]]]:
        """The sweep off the event loop; a real one holds new work back until it finishes, even if the caller leaves."""
        self._check_worktree_sweep()
        if dry_run:
            return await asyncio.to_thread(self.sweep_worktrees, True)
        self._worktree_sweep_done.clear()
        operation = asyncio.create_task(asyncio.to_thread(self.sweep_worktrees))
        operation.add_done_callback(lambda _: self._worktree_sweep_done.set())
        return await asyncio.shield(operation)

    async def bot_worktree_sweep(self, task_id: str, dry_run: bool = True) -> dict[str, list[dict[str, str]]]:
        if not self.is_coordinator(self.store.get_task(task_id).bot):
            raise NotAllowed("only coordinators can sweep worktrees")
        return await self.run_worktree_sweep(dry_run)

    def _turns_over(self, task: Task) -> None:
        """A task's turns are over for now: a thread's end is news for the crew, and a folder its own handoff dropped
        can go."""
        if task.kind == "thread" and not task.active and not self._background(task.id):
            self.happened("thread_done", task.id, f"{task.bot}'s thread {task.title!r} is {task.status.value}")
        if task.merge_state == "dropped" and task.worktree_path:
            self._drop_checkout(task)
        if self._in_review_copy(task) and not task.active:  # the review's throwaway copy: made again if it runs again
            try:
                worktree.remove_copy(Path(self.store.get_task(task.review_of).repo), Path(task.workspace))
            except (worktree.GitError, OSError):
                log.exception("Could not remove the review copy %s", task.workspace)

    def handoff(self, task_id: str, done: str, output: list[str], open: str, needs: str,
                evidence: list[str] | None = None, checks: list[str] | None = None) -> Handoff:
        """Work handed back. Code work (a task with a branch) carries its evidence: what was run or seen to prove it
        works and what it showed, or what couldn't be checked and why. Whoever checks it next reruns what matters
        rather than taking the author's word, and "tests pass" alone isn't evidence of what the change does."""
        task = self.store.get_task(task_id)
        evidence = [line.strip() for line in evidence or [] if line.strip()]
        if task.branch and not evidence and not DROPPED.match(done):
            raise NotAllowed("hand back your evidence too: what you ran or looked at to prove it works and what it "
                             "showed (a command and its output, a measurement, a screenshot you shared), or what you "
                             "couldn't check and why. For crewbots, the verify-crewbots skill says how")
        checks = [line.strip() for line in checks or [] if line.strip()][:MAX_CHECKS]
        handoff = self.store.add_handoff(task_id, task.bot, done, output, open, needs, evidence, checks)
        self._record(task_id, kind="handoff", data=handoff.to_json())
        if (dropped := DROPPED.match(done)) and task.kind == "thread" and task.merge_state in (None, "dropped"):
            reason = next((line for line in dropped[1].splitlines() if line.strip()), "dropped by its own bot")
            self._mark_dropped(task, task.bot, reason)  # its folder goes once the turn is over
        self._close_questions(task_id)
        self._refresh_working_state(task.bot)
        self.crew.memory.log(task.bot, f"{task_id} {_short(task.title, 80)}: {_short(done, 200)}"
                             + (f" Open: {_short(open, 120)}" if open.strip() else ""))
        self._report_late(task_id)
        return handoff

    def _check_seat_answer(self, task: Task, question_id: int | None) -> None:
        asked = self.store.last_event(task.id, ("escalation",))
        resolved = asked and self.store.db.execute(
            "select 1 from events where task_id = ? and id > ? and "
            "(kind = 'questions_closed' or (kind = 'message' and json_extract(data, '$.from') = 'user')) limit 1",
            (task.id, asked.id)).fetchone()
        room_exists = task.room_id and self.store.db.execute("select 1 from rooms where id = ?", (task.room_id,)).fetchone()
        if question_id is None and (not asked or resolved) and room_exists:
            return
        if (not asked or question_id != asked.id or resolved or not room_exists
                or task.status in (Status.FAILED, Status.CANCELLED)):
            raise NotAllowed("This question is closed or has been replaced. Please use the current question.")

    def _close_seat_questions(self, task_id: str) -> None:
        task = self.store.get_task(task_id)
        chat = self.store.chat_of(task.bot) if task.kind == "seat" else None
        if not chat:
            return
        rows = self.store.db.execute(
            "select json_extract(e.data, '$.question_id') as question_id from events e "
            "where e.task_id = ? and e.kind = 'seat_escalation' and json_extract(e.data, '$.task') = ? "
            "and not exists (select 1 from events a where a.task_id = e.task_id "
            "and a.kind in ('seat_escalation_answer', 'seat_escalation_closed') "
            "and json_extract(a.data, '$.question_id') = json_extract(e.data, '$.question_id'))",
            (chat.id, task_id)).fetchall()
        for row in rows:
            self._record(chat.id, kind="seat_escalation_closed", data={"question_id": row["question_id"]})

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
            where = "chat" if task.kind in ("seat", "chat") else "thread"
            answer = f"answered in {where} on {datetime.now().astimezone().date()} (task {task.id})"
        try:
            from crewbots.working_state import edit

            edit(self.crew.memory.root / task.bot / "working-state" / "state.md", "Decisions", "add",
                 f"{timefmt.minute(event.at)[:10]} {task.id}: {question} — {answer}", automatic=True)
        except (MemoryError, OSError) as e:
            self._record(task.id, kind="notice", data={"message": f"Could not record decision: {e}"})
        return True, nudge

    def escalate(self, task_id: str, question: str, choices: list[Any] | None = None, multiple: bool = False) -> None:
        """Marks the task as waiting on an answer: a decision that isn't its bot's, or what's wanted when it's unsure.
        With `choices`, the user picks one (or several, if `multiple`), or answers in their own words. Its parent
        hears at once; a top-level task waits for the user."""
        task = self.store.get_task(task_id)
        if task_id in self._cancelling or task.status is Status.CANCELLED:
            raise NotAllowed("This task is cancelled or being cancelled.")
        offered = _choices(choices)
        if task.kind == "seat" and task.room_id:
            try:
                room = self._room_to_post(task, task.room_id)
            except NotAllowed:
                room = None
            self._close_questions(task_id)
            chief = self.coordinator()
            recipient = (chief.name if chief and chief.name in room["members"] else room["lead"]) if room else None
            if room and not (chief and task.bot == chief.name) and task.bot != recipient:
                listed = "".join(f"\n- {c['label']}" + (f": {c['description']}" if "description" in c else "")
                                 for c in offered)
                self.post(room["id"], task.bot, f"@{recipient} {question}"
                          + (f"\n\nChoices{' (any of them)' if multiple else ''}:{listed}" if offered else "")
                          + f"\n\nPlease reply to @{task.bot} in this room.",
                          depth=-1, recipients=[recipient])
                return
        self._escalated.add(task_id)
        self._record(task_id, kind="escalation", data={"question": question} | (
            {"choices": offered, "multiple": bool(multiple)} if offered else {}))
        if task.kind == "seat" and task.room_id:
            asked = self.store.last_event(task_id, ("escalation",))
            self._record(self.chat(task.bot).id, kind="seat_escalation", data={
                "task": task.id, "question_id": asked.id, "room": self.store.room(task.room_id)["title"],
                "question": question, "choices": offered, "multiple": bool(multiple)})
        self._refresh_working_state(task.bot)
        if task.parent_id:
            parent = self.store.get_task(task.parent_id)
            chief = self.coordinator()
            if task.kind != "seat" and parent.kind == "seat" and parent.room_id and chief and parent.bot == chief.name:
                self._chief_room_item(self.store.room(parent.room_id), task.bot, "escalated", f"task {task.id}")
            listed = "".join(f"\n- {c['label']}" + (f": {c['description']}" if "description" in c else "") for c in offered)
            if task.kind != "seat":
                self._say_in_room(task, task.bot, parent.bot, f"{task_id} needs an answer: {question}"
                                  + (f"\n\nChoices{' (any of them)' if multiple else ''}:{listed}" if offered else ""))
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


def _mentions(match: str, text: str) -> bool:
    """Whether `text` has every word of `match` (any text does, for no match)."""
    return all(word in text.lower() for word in match.lower().split())
