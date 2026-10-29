"""Where a task works: a worktree of its own, or a folder as it is."""

from pathlib import Path

from crewbots import worktree
from crewbots.backends import Access
from crewbots.crew import Bot
from crewbots.runner.common import NotAllowed
from crewbots.store import Task


class Checkouts:
    def _cwd(self, task: Task, bot: Bot) -> Path:
        if task.review_of:
            reviewed = self.store.get_task(task.review_of)
            if reviewed.worktree_path and task.workspace == reviewed.worktree_path:
                self._restore_worktree(reviewed)
            if self._in_review_copy(task) and not Path(task.workspace).exists():  # removed after an earlier turn
                if reviewed.worktree_path and not Path(reviewed.worktree_path).exists():
                    self._restore_worktree(reviewed)
                self._review_copy(reviewed, Path(task.workspace))
        if task.worktree_path:
            self._restore_worktree(task)
            return Path(task.worktree_path)
        workspace = Path(task.workspace).expanduser() if task.workspace else self.crew.folder_of(bot)
        if not task.workspace and not bot.workspace:
            workspace.mkdir(parents=True, exist_ok=True)
        if not workspace.is_dir():
            raise FileNotFoundError(f"workspace {workspace} does not exist")
        if bot.access is Access.READ_ONLY or task.kind == "ask":
            return workspace
        if not bot.worktree or not worktree.is_repo(workspace):
            return self._in_place(workspace)
        matching = [p for p in self.crew.projects.values() if workspace.resolve() == p.folder.resolve()]
        if matching and matching[0].isolation == "in_place":  # beside the user and other bots, on its current branch
            return self._in_place(workspace)
        wt = worktree.create(workspace, task.id, root=self.worktree_root)
        self._set(task.id, repo=str(wt.repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base)
        self._record(task.id, kind="worktree", data={"path": str(wt.path), "branch": wt.branch, "base": wt.base})
        return wt.path

    def _in_place(self, workspace: Path) -> Path:
        home, folder = self.crew.home.resolve(), workspace.resolve()
        if home.is_relative_to(folder) or folder.is_relative_to(home):
            raise NotAllowed(f"{workspace} holds the crew's own settings, and a bot that writes can't work there; "
                             "give it a folder of its own")
        return workspace
