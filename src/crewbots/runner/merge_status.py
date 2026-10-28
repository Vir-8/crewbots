"""Whether a proposed task's changes are already on the branch they would merge into."""

from pathlib import Path

from crewbots import worktree
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
