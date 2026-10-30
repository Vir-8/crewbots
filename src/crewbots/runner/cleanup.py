"""Worktree maintenance: a task's folder removed once its work is merged, withdrawn or dropped, with anything it hadn't
committed saved to its branch first; and the sweep that does it for every task with a folder."""

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from crewbots import worktree
from crewbots.runner.common import NotAllowed
from crewbots.store import Status, Task

log = logging.getLogger(__name__)


class Cleanup:
    """The part of Runner that removes task worktrees, one at a time or in a sweep."""

    def _check_worktree_sweep(self, task_id: str | None = None) -> None:
        if not self._worktree_sweep_done.is_set():
            raise NotAllowed("Worktree maintenance is running; try again when it finishes.")
        self.check_not_merging(task_id)  # the task's own merge, for withdraw and drop

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

    def _cleanup_after_action(self, task: Task) -> None:
        try:
            result = self._cleanup_worktree(task)
            if result["status"] != "removed":
                log.warning("Worktree cleanup for %s: %s", task.id, result.get("reason"))
        except Exception:
            log.exception("Could not clean up worktree for %s", task.id)

    def _cleanup_worktree(self, task: Task, *, sweep: bool = False, dry_run: bool = False) -> dict[str, str]:
        result = {"task_id": task.id, "path": task.worktree_path or "", "branch": task.branch or ""}
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
            if not dry_run:
                self.close_editor(wt.path)  # a Unity Editor on it would write there as it goes
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
                return result | {"status": "would_remove", "branch_status": "kept", "reason": _removal_notes(task, wt)}
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


def _removal_notes(task: Task, wt: worktree.Worktree) -> str:
    """What removing a task's worktree would do, for a dry run."""
    notes = []
    # --no-optional-locks is GIT_OPTIONAL_LOCKS=0: a dry run must not rewrite the index.
    if worktree._git(wt.path, "--no-optional-locks", "status", "--porcelain", "--ignore-submodules=none"):
        notes.append("uncommitted changes would be saved to the branch first")
    ahead = worktree._git(wt.repo, "rev-list", "--count", f"{wt.base}..{wt.branch}")
    if ahead != "0" and task.merge_state != "merged":
        notes.append(f"{ahead} unmerged commit(s) stay on the branch")
    if task.merge_state == "merged" and task.merged_tip:
        later = worktree._git(wt.repo, "rev-list", "--count", f"{task.merged_tip}..{wt.branch}")
        if later != "0":
            notes.append(f"merged, {later} later commit(s) kept on the branch")
    return "; ".join(notes) or ("merged and clean" if task.merge_state == "merged" else "clean, nothing unmerged")
