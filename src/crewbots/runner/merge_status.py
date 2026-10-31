"""Merging, one at a time and gated on CI. Merges run in a worker thread under MERGE_LOCK, so a long CI run doesn't
freeze the daemon and two merges never share a checkout (`landed.py`'s revert takes the same lock). Here too: whether a
proposed task's changes are already on the branch they'd merge into, and what to tell a task whose worktree has left
its own branch."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from crewbots import ci, worktree
from crewbots.crew import is_no_reply
from crewbots.events import Notice
from crewbots.runner.common import MergeError, NotAllowed
from crewbots.store import Task

MERGE_LOCK = threading.Lock()  # one merge at a time, in the daemon
_MERGING_LOCK = threading.Lock()  # guards the per-task in-flight counts
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="merge")


class MergeStatus:
    def already_on(self, task: Task) -> str | None:
        """The branch a proposed task's changes are already part of, or None. Reads Git only, without writing to
        the repository or touching the project folder, so a folder with uncommitted changes of its own does not
        hide it."""
        if task.merge_state != "proposed" or not (task.repo and task.branch and task.base):
            return None
        repo = Path(task.repo)
        try:
            tip = worktree.git_read(repo, "rev-parse", "--verify", f"refs/heads/{task.branch}")
            if task.worktree_path and Path(task.worktree_path).is_dir() and worktree.git_read(
                    Path(task.worktree_path), "status", "--porcelain"):
                return None  # uncommitted work the branch does not yet hold
            into = (self._in_folder(task) and self._folder_from(task)) or worktree.git_read(
                repo, "symbolic-ref", "--short", "HEAD")
            return into if worktree.already_in(repo, f"refs/heads/{into}", tip, task.base) else None
        except worktree.GitError:
            return None

    def merged_details(self, task: Task) -> dict[str, Any]:
        """What the merge that landed the task recorded (its commit, files, what git ignored): {} when it isn't
        merged, or its latest merged event names another commit."""
        event = self.store.last_event(task.id, ("merged",)) if task.merge_state == "merged" else None
        return event.data if event and event.data.get("commit") == task.merged_commit else {}

    def ci_gate(self, task: Task, into: str, tip: str) -> tuple[str, str]:
        """Run the repo's CI steps on what a merge would land on `into`: the squash of `tip` on top of `into`, built
        with merge-tree so no ref or file moves. Returns (branch, commit of it) that was tested. Raises MergeError
        naming the failing step, or if the result can't be built (a conflict included): CI is never skipped. A repo
        with no CI steps passes. Blocking, like the merge."""
        repo = Path(task.repo)
        target = f"refs/heads/{into}"
        try:
            tested = worktree._git(repo, "rev-parse", "--verify", target)
            tree = worktree.git_read(repo, "merge-tree", "--write-tree", target, tip).split("\n", 1)[0].strip()
            commit = worktree._git(repo, "-c", "user.name=crewbots", "-c", "user.email=crewbots@localhost",
                                   "commit-tree", tree, "-p", target, "-m", "merge check")
        except worktree.GitError as e:
            if "CONFLICT" in str(e):  # no result to check: refuse, with the conflict as the reason
                raise MergeError(f"merging {task.branch} into {into} failed, and nothing was changed: {e}") from None
            raise MergeError(f"could not build the merged result to run CI on, so nothing was merged: {e}") from None
        project = self.crew.project_at(Path(task.workspace or repo))  # its folder may be a subfolder of the repository
        within = project.folder.resolve().relative_to(repo.resolve()) if project else Path()
        try:
            ci.check(repo, commit, into, project.ci if project else (), directory=str(within))
        except ci.CIFailed as e:
            raise MergeError(f"CI did not pass on the merged result, so nothing was merged: {e}") from None
        return into, tested

    async def merge_off_loop(self, task_id: str, force: bool = False):
        """Runner.merge in a thread, so a long CI run doesn't freeze the daemon."""
        return await self._off_loop(task_id, self.merge, task_id, force)

    async def propose_merge_off_loop(self, parent_id: str, task_id: str, summary: str):
        return await self._off_loop(task_id, self.propose_merge, parent_id, task_id, summary)

    async def _off_loop(self, task_id: str, call, *args):
        """Merges run one at a time, whichever task: the worker thread holds MERGE_LOCK for the whole operation (CI and
        landing), so a caller that gives up can't release it, a retry waits its turn (and finds the task merged), and
        two merges never use one checkout together. Until the worker finishes, the task counts as being merged."""
        merging = self.__dict__.setdefault("_merging_now", {})
        with _MERGING_LOCK:
            merging[task_id] = merging.get(task_id, 0) + 1

        def worker():
            with MERGE_LOCK:
                return call(*args)

        def finished(_):  # runs once: when the worker ends, or when a still-queued job is cancelled
            with _MERGING_LOCK:
                merging[task_id] -= 1

        try:
            cf = _EXECUTOR.submit(worker)
        except BaseException:
            finished(None)
            raise
        cf.add_done_callback(finished)
        return await asyncio.wrap_future(cf)

    def check_not_merging(self, task_id: str | None) -> None:
        """Refuse to withdraw or drop a task while a merge of it is waiting or running."""
        if task_id and self.__dict__.get("_merging_now", {}).get(task_id):
            raise NotAllowed("a merge of this task is running; try again when it finishes")

    def merge_gated(self, task: Task, wt: worktree.Worktree, message: str) -> tuple[str, str]:
        """Run CI on the squash of the worktree's committed tip, then land it; returns (tip, merge commit).
        worktree.merge checks that the branch CI tested is still where it was. If main moved during CI, CI runs once
        more on the new squash; if that moves again too, the merge is refused. A proposal that changed while CI ran
        (withdrawn) is not landed."""
        for attempt in (1, 2):
            # In the folder itself, the folder is back on the branch it merges into by now (runner/folder_branch.py).
            tip = worktree._git(wt.path, "rev-parse", f"refs/heads/{wt.branch}" if self._in_folder(task) else "HEAD")
            tested = self.ci_gate(task, worktree._git(wt.repo, "symbolic-ref", "--short", "HEAD"), tip)
            if self.store.get_task(task.id).merge_state != task.merge_state:
                raise MergeError("the proposal changed while CI ran, so nothing was merged; propose again")
            try:
                return tip, worktree.merge(wt, message, tip=tip, tested=tested)
            except worktree.Moved as e:
                if attempt == 2:
                    raise worktree.GitError(f"{e}, and again during the second CI run, so nothing was merged; "
                                            "propose again") from None
        raise AssertionError("unreachable")

    def wrong_branch(self, task: Task) -> str | None:
        """What to tell a worktree task whose HEAD is not on its own branch (another branch, or detached), or None."""
        if not (task.worktree_path and task.branch) or not Path(task.worktree_path).is_dir():
            return None
        try:
            head = worktree.git_read(Path(task.worktree_path), "symbolic-ref", "--short", "HEAD")
        except worktree.GitError:
            head = None
        if head == task.branch:
            return None
        return (f"Your worktree is {f'on {head}' if head else 'on a detached HEAD'}, not on its task branch {task.branch}. "
                f"Switch back with `git switch {task.branch}`, bringing over any commits you made elsewhere. "
                "Until then the work cannot be proposed for merging.")

    def _checked(self, task: Task, result: str) -> str:
        """A finished turn's result, with the switch-back request added when its worktree is on the wrong branch.
        A reply of NO_REPLY said nothing, so it is no result."""
        if is_no_reply(result):
            result = ""
        if not (warning := self.wrong_branch(task)):
            return result
        self._record(task.id, Notice(message=warning))
        return f"{result}\n\n{warning}".strip()

    def foreign_head_removable(self, task: Task, wt: worktree.Worktree, head: str | None) -> str:
        """The branch that already holds the work of a task whose clean worktree sits on another crewbots/* branch.
        Rewritten and squashed history leaves no ancestry, so this asks only whether merging the worktree's HEAD
        into the current branch would change nothing (worktree.already_in); a conflict or a different tree keeps it.
        Raises GitError with the reason when the worktree has to stay."""
        if not (head and head.startswith(worktree.BRANCH_PREFIX)):
            raise worktree.GitError(f"worktree is {f'on {head}' if head else 'on a detached HEAD'}, not the task branch")
        if worktree.git_read(wt.path, "status", "--porcelain", "--ignore-submodules=none"):
            raise worktree.GitError(f"worktree is on {head}, not the task branch, and has uncommitted changes")
        into = worktree.git_read(wt.repo, "symbolic-ref", "--short", "HEAD")
        if not worktree.already_in(wt.repo, f"refs/heads/{into}", worktree.git_read(wt.path, "rev-parse", "HEAD"), task.base):
            raise worktree.GitError(f"worktree is on {head}, not the task branch, and it holds changes not on {into}")
        return into
