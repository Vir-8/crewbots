"""What landed: work merged into a project, most of it by crewbots on its own once reviews passed, for the user to look
over after the fact rather than before, with the evidence it was merged on and a revert for what shouldn't stay."""

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crewbots import worktree
from crewbots.runner.common import NotAllowed, _iso
from crewbots.runner.merge_status import MERGE_LOCK
from crewbots.store import Task

LANDED = 50  # merges listed, the latest first


class Landed:
    """The part of Runner that lists what landed and reverts it."""

    def landed(self) -> dict[str, Any]:
        """The latest merges, each with its evidence, verdicts and revert, and when the user last looked."""
        seen = self.store.landed_seen()
        items = []
        for task, at, merged in self.store.merged_tasks(LANDED):
            handoff = self.store.latest_handoff(task.id)
            reviews = self.store.reviewers_of(task.id)
            last = max((r.review_version or 0 for r in reviews), default=0)  # the version that merged
            reviews = [r for r in reviews if (r.review_version or 0) == last]
            verdicts = [{"bot": r.bot, "verdict": v.verdict.value, "summary": v.summary}
                        for r in reviews if (v := self.store.latest_review(r.id))]
            reverted = self.store.last_event(task.id, ("reverted",))
            items.append({"task": task.to_json(), "merged_at": at, "commit": merged.get("commit"),
                          "into": merged.get("into"), "automatic": bool(merged.get("automatic")),
                          "evidence": handoff.evidence if handoff else [], "verdicts": verdicts,
                          "reverted": reverted.data if reverted else None})
        return {"items": items, "seen_at": _iso(datetime.fromtimestamp(seen / 1000, UTC)) if seen else None}

    def landed_unseen(self) -> int:
        """How many merges came after the user last looked."""
        seen = self.store.landed_seen()
        since = _iso(datetime.fromtimestamp(seen / 1000, UTC)) if seen else ""
        return sum(1 for _, at, _ in self.store.merged_tasks(LANDED) if at > since)

    def mark_landed_seen(self) -> None:
        self.store.set_landed_seen(int(time.time() * 1000))
        self.bus.publish({"type": "crew", "crew": self.state()})

    def revert(self, task_id: str) -> Task:
        """The user's revert of merged work: `git revert` of its merge commit in the folder it went into, as a commit of
        its own. Other uncommitted work in the folder, the user's or the crew's, is left as it is: git refuses, changing
        nothing, only when a file the revert touches has uncommitted changes or something is staged. Refused too while
        a merge is running; on a conflict the revert is abandoned and nothing changes."""
        task = self.store.get_task(task_id)
        merged = self.store.last_event(task_id, ("merged",))
        if task.merge_state != "merged" or not merged or not merged.data.get("commit"):
            raise NotAllowed(f"{task_id} hasn't merged, so there's nothing to revert")
        if self.store.last_event(task_id, ("reverted",)):
            raise NotAllowed(f"{task_id} was reverted already")
        commit, repo = merged.data["commit"], Path(merged.data["into"])
        if not MERGE_LOCK.acquire(blocking=False):
            raise NotAllowed("a merge is running: try again in a moment")
        try:
            try:
                worktree._git(repo, "merge-base", "--is-ancestor", commit, "HEAD")
            except worktree.GitError:
                raise NotAllowed(f"{commit[:7]} isn't in {repo}'s current branch") from None
            try:
                worktree._git(repo, "revert", "--no-edit", commit)
            except worktree.GitError as e:
                if "would be overwritten" in str(e):  # refused before it began: nothing to abandon
                    raise NotAllowed(f"reverting {commit[:7]} would touch files with uncommitted changes, or something is "
                                     f"staged in {repo}: commit or set those aside, then revert") from None
                try:
                    worktree._git(repo, "revert", "--abort")
                except worktree.GitError:
                    pass
                raise NotAllowed(f"reverting {commit[:7]} conflicts with what came after it: {e}") from None
            revert_commit = worktree._git(repo, "rev-parse", "HEAD")
        finally:
            MERGE_LOCK.release()
        self._record(task_id, kind="reverted", data={"commit": commit, "revert_commit": revert_commit, "by": "user"})
        self.bus.publish({"type": "task", "task": task.to_json()})
        return task
