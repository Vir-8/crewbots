"""Room for worktrees, and the folder's own branch, in a project whose tasks work in worktrees.

A project can cap its task worktrees (projects.py `max_worktrees`), since each costs the disk space of a checkout: a
task that would make one more is refused, when it's handed out and again when its first turn would make it, with the
tasks that hold them named.

It can also let one task at a time work in the folder itself, on a branch of its own (`folder_branch`), which takes no
worktree: asked for with `in_folder`, the task's first turn switches the folder to `crewbots/<task id>` from the branch
it was on. Its worktree path is then the folder, and the rest of crewbots treats it as a worktree whose main checkout
is itself. Its merge switches the folder back first and squashes the branch in there; dropping or withdrawing it saves
what it hadn't committed to its branch and switches back; a later turn switches to its branch again, if the folder is
free. The folder has to be clean to be taken, so none of the user's own work ends up on a task's branch."""

import logging
from pathlib import Path

from crewbots import worktree
from crewbots.backends import Access
from crewbots.projects import Project
from crewbots.runner.common import NotAllowed
from crewbots.store import Status, Task

ASKED = "folder_asked"  # handed out with in_folder; its first turn takes the folder
TAKEN = "folder_branch"  # the folder switched to the task's branch, and from which branch

log = logging.getLogger(__name__)


class FolderBranch:
    """The part of Runner that caps a project's worktrees and lends its folder to one task at a time."""

    def _writes_in_worktree(self, bot_name: str) -> bool:
        bot = self.bot_for(bot_name)
        return bool(bot and bot.worktree and bot.access is not Access.READ_ONLY)

    def _not_started(self, folder: Path) -> list[Task]:
        """Work handed out in `folder` that hasn't started, so has no checkout yet but will make one."""
        rows = self.store.db.execute(
            "select id from tasks where workspace = ? and worktree_path is null and kind = 'thread' and review_of is null"
            " and status in (?, ?)", (str(folder), Status.QUEUED.value, Status.HELD.value)).fetchall()
        return [t for t in map(self.store.get_task, (r[0] for r in rows)) if self._writes_in_worktree(t.bot)]

    def _worktrees_of(self, top: Path) -> list[Task]:
        """The tasks with a worktree of the repository at `top` on disk, the folder's own branch aside."""
        rows = self.store.db.execute("select id, repo, worktree_path from tasks where worktree_path is not null")
        return [self.store.get_task(id) for id, repo, path in rows.fetchall()
                if repo and Path(repo).resolve() == top and Path(path).resolve() != top and Path(path).is_dir()]

    def _worktree_room(self, folder: Path, *, but: str | None = None, waiting: bool = False) -> None:
        """NotAllowed if the project in `folder` has as many task worktrees as it allows. `waiting` counts the work
        handed out there that hasn't made its own yet."""
        project = self.crew.project_at(folder)
        if not (project and project.max_worktrees and project.isolation == "worktree" and worktree.is_repo(folder)):
            return
        top = Path(worktree._git(folder, "rev-parse", "--show-toplevel")).resolve()
        holders = [t for t in self._worktrees_of(top) if t.id != but]
        holders += [t for t in self._not_started(project.folder) if t.id != but and not self._asked_folder(t)] if waiting else []
        if len(holders) < project.max_worktrees:
            return
        named = ", ".join(f"{t.id} ({t.bot}, {t.status.value})" for t in holders)
        cap = f"{project.max_worktrees} task worktree" + ("s" if project.max_worktrees > 1 else "")
        free = project.folder_branch and not self._folder_refusal(project)
        raise NotAllowed(
            f"{project.name} allows at most {cap} at once (the user's setting, for disk space), and "
            f"{named} {'have them' if len(holders) > 1 else 'has it'}. Hand this to one of those tasks as a follow-up "
            "(message), close one that's done (merge it, or drop_task), "
            + ("give it the folder's own branch with in_folder, " if free else "") + "or wait for one to finish.")

    def _asked_folder(self, task: Task) -> bool:
        return self.store.last_event(task.id, (ASKED,)) is not None

    def _in_folder(self, task: Task) -> bool:
        """Whether the task's checkout is its project's folder itself, on a branch of its own."""
        return bool(task.worktree_path and task.repo and Path(task.worktree_path).resolve() == Path(task.repo).resolve())

    def _folder_from(self, task: Task) -> str | None:
        """The branch the folder was on when the task last took it: where its work is merged, and what it goes back to."""
        taken = self.store.last_event(task.id, (TAKEN,))
        return taken.data.get("from") if taken else None

    def _folder_holder(self, project: Project, top: Path) -> Task | None:
        """The task whose branch the project's folder is on, or the one waiting to take it."""
        try:
            head = worktree.git_read(top, "symbolic-ref", "--short", "HEAD")
        except worktree.GitError:
            head = ""
        if head.startswith(worktree.BRANCH_PREFIX):
            try:
                task = self.store.get_task(head.removeprefix(worktree.BRANCH_PREFIX))
            except KeyError:
                task = None
            if task and self._in_folder(task) and Path(task.repo).resolve() == top:
                return task
        return next((t for t in self._not_started(project.folder) if self._asked_folder(t)), None)

    def _folder_refusal(self, project: Project, task: Task | None = None) -> str | None:
        """Why `task` (or new work) can't have the project's folder on a branch of its own now, or None."""
        folder = project.folder
        if project.isolation != "worktree" or not project.folder_branch:
            return (f"{project.name} doesn't lend its folder to a task: the user turns that on in the app, under Projects"
                    if project.isolation == "worktree" else f"{project.name} is worked in its folder itself already")
        if not folder.is_dir() or not worktree.is_repo(folder):
            return f"{folder} isn't a git repository"
        top = Path(worktree._git(folder, "rev-parse", "--show-toplevel")).resolve()
        holder = self._folder_holder(project, top)
        if holder and (task is None or holder.id != task.id):
            return (f"{holder.id} ({holder.bot}) has {project.name}'s folder on its branch; one task at a time works "
                    "there, until its work is merged or dropped")
        try:
            head = worktree.git_read(top, "symbolic-ref", "--short", "HEAD")
        except worktree.GitError:
            return f"{folder} is on a detached HEAD; the user checks out a branch there first"
        if task and head == task.branch:
            return None
        if head.startswith(worktree.BRANCH_PREFIX):
            return f"{folder} is on {head}, a task's branch, not one of the user's; the user switches it back first"
        if changed := worktree.git_read(top, "status", "--porcelain").splitlines():
            return (f"{folder} has {len(changed)} uncommitted change(s), and a task only takes the folder when it's "
                    "clean, so none of the user's work goes onto its branch")
        return None

    def _take_folder(self, task: Task, project: Project) -> Path:
        """Switches the project's folder to the task's branch: made from the branch the folder is on, the first time."""
        if refused := self._folder_refusal(project, task):
            raise NotAllowed(refused)
        top = Path(worktree._git(project.folder, "rev-parse", "--show-toplevel"))
        start = worktree._git(top, "symbolic-ref", "--short", "HEAD")
        branch = task.branch or worktree.BRANCH_PREFIX + task.id
        if start == branch:
            return top
        exists = worktree.git_read(top, "branch", "--list", branch)
        worktree._git(top, "switch", *([branch] if exists else ["-c", branch]))
        base = task.base if exists and task.base else worktree._git(top, "rev-parse", "HEAD")
        self._set(task.id, repo=str(top), worktree_path=str(top), branch=branch, base=base)
        self._record(task.id, kind=TAKEN, data={"path": str(top), "branch": branch, "base": base, "from": start})
        return top

    def _leave_folder(self, task: Task, wt: worktree.Worktree) -> None:
        """Switches the folder back to the branch it was on before the task took it, if it's still on the task's."""
        try:
            head = worktree._git(wt.path, "symbolic-ref", "--short", "HEAD")
        except worktree.GitError:
            return
        if head == wt.branch and (start := self._folder_from(task)):
            worktree._git(wt.path, "switch", start)

    def _back_to_work(self, wt: worktree.Worktree) -> None:
        """After a merge that didn't land, the folder goes back to the task's branch, so its work can go on."""
        try:
            worktree._git(wt.path, "switch", wt.branch)
        except worktree.GitError:
            log.exception("Could not switch %s back to %s", wt.path, wt.branch)

    def _release_folder(self, task: Task, wt: worktree.Worktree, *, sweep: bool, dry_run: bool) -> dict[str, str]:
        """cleanup.py's removal, for work in the folder itself: what it hadn't committed is saved to its branch, and
        the folder goes back to the branch it was on. A merged branch is deleted, as a removed worktree's is."""
        if dry_run:
            return {"status": "would_remove", "branch_status": "kept", "reason": "the folder goes back to "
                    f"{self._folder_from(task) or 'its branch'}"}
        try:
            head = worktree._git(wt.path, "symbolic-ref", "--short", "HEAD")
        except worktree.GitError:
            head = None
        if head == wt.branch:
            worktree.commit(wt, f"Uncommitted work from {task.id}, saved on leaving the folder")
            self._leave_folder(task, wt)
        deleted = False
        if task.merge_state == "merged" and not sweep and task.merged_commit:
            tip = worktree._git(wt.repo, "rev-parse", f"refs/heads/{wt.branch}")
            worktree.branch_merged(wt.repo, tip, task.merged_tip, task.merged_commit)
            worktree._git(wt.repo, "update-ref", "-d", f"refs/heads/{wt.branch}", tip)
            deleted = True
        self._record(task.id, kind="folder_left", data={"path": str(wt.path), "to": self._folder_from(task)})
        return {"status": "removed", "branch_status": "deleted" if deleted else "kept"}
