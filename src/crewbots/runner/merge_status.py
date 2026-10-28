"""Whether a proposed task's changes are already on the branch they would merge into."""

from pathlib import Path

from crewbots import worktree
from crewbots.crew import is_no_reply
from crewbots.events import Notice
from crewbots.store import Task


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
            if task.base_branch:
                if worktree.git_read(repo, "symbolic-ref", "--short", "HEAD") == task.branch and worktree.git_read(
                        repo, "status", "--porcelain"):
                    return None  # the folder holds work the branch does not yet
                into = task.base_branch
            else:
                if task.worktree_path and Path(task.worktree_path).is_dir() and worktree.git_read(
                        Path(task.worktree_path), "status", "--porcelain"):
                    return None  # uncommitted work the branch does not yet hold
                into = worktree.git_read(repo, "symbolic-ref", "--short", "HEAD")
            return into if worktree.already_in(repo, f"refs/heads/{into}", tip, task.base) else None
        except worktree.GitError:
            return None

    def wrong_branch(self, task: Task) -> str | None:
        """What to tell a worktree task whose HEAD is not on its own branch (another branch, or detached), or None."""
        if task.base_branch or not (task.worktree_path and task.branch) or not Path(task.worktree_path).is_dir():
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
