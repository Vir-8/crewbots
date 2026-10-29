"""Project checkout selection and branch-mode recovery."""

import logging
from pathlib import Path

from crewbots import branch_mode, worktree
from crewbots.backends import Access
from crewbots.crew import Bot
from crewbots.events import Notice
from crewbots.runner.common import NotAllowed
from crewbots.store import Status, Task


class Branches:
    def _branch_project(self, bot: str, workspace: str | None, kind: str, review_of: str | None):
        target = Path(workspace).expanduser() if workspace else self.crew.folder_of(self.crew.bots[bot])
        project = next((p for p in self.crew.projects.values()
                        if p.folder.resolve() == target.resolve() and p.isolation == "branch"
                        and (p.folder / "ProjectSettings" / "ProjectVersion.txt").is_file()), None)
        if not (project and kind == "thread" and not review_of and self.crew.bots[bot].worktree
                and self.crew.bots[bot].access is not Access.READ_ONLY):
            return None
        try:
            holder = self.store.db.execute(
                "select id from tasks where repo = ? and base_branch is not null and worktree_path is not null "
                "and (merge_state is null or merge_state != 'merged') order by created_at limit 1",
                (str(target.resolve()),)).fetchone()
            if holder:
                raise NotAllowed(f"{project.name} already has a task running in branch mode: {holder['id']}; "
                                 f"dropping {holder['id']} releases the project")
            current = worktree._git(target, "symbolic-ref", "--short", "HEAD")
            if current.startswith(worktree.BRANCH_PREFIX):
                raise NotAllowed(f"{project.name} already has a task running in branch mode: "
                                 f"{current.removeprefix(worktree.BRANCH_PREFIX)}; "
                                 f"dropping {current.removeprefix(worktree.BRANCH_PREFIX)} releases the project")
            branch_mode.prepare(target, "preflight")
        except worktree.GitError as e:
            raise NotAllowed(str(e)) from None
        return project

    def _start_branch(self, task: Task, project) -> Task:
        try:
            checkout = branch_mode.prepare(project.folder, task.id)
            task = self.store.update_task(task.id, repo=str(checkout.repo), worktree_path=str(checkout.repo),
                                          branch=checkout.branch, base=checkout.base,
                                          base_branch=checkout.original_branch, checkout_state="starting")
            worktree._git(checkout.repo, "switch", "-c", checkout.branch)
            task = self.store.update_task(task.id, checkout_state=None)
        except worktree.GitError as e:
            self.store.update_task(task.id, status=Status.FAILED, error=str(e), repo=None, worktree_path=None,
                                   branch=None, base=None, base_branch=None, checkout_state=None)
            raise NotAllowed(str(e)) from None
        return task

    def _recover_branch_projects(self) -> None:
        self._recover_starting()
        self._recover_merges()
        for project in self.crew.projects.values():
            if project.isolation != "branch" or not (project.folder / "ProjectSettings" / "ProjectVersion.txt").is_file():
                continue
            try:
                branch = worktree._git(project.folder, "symbolic-ref", "--short", "HEAD")
                if not branch.startswith(worktree.BRANCH_PREFIX):
                    continue
                task_id = branch.removeprefix(worktree.BRANCH_PREFIX)
                task = self.store.get_task(task_id)
                if (task.status in {Status.FAILED, Status.CANCELLED} or task.merge_state == "merged"
                        or task.branch != branch or not task.worktree_path
                        or not task.base_branch or Path(task.repo).resolve() != project.folder.resolve()):
                    if task.merge_state == "merged" and task.branch == branch:
                        raise ValueError(f"{task_id} is merged but the project is still on its branch; "
                                         "drop the task to switch back to the base branch")
                    raise ValueError("task is closed or its checkout metadata is incomplete")
                logging.getLogger(__name__).info("Recovered branch-mode project %s for %s", project.name, task_id)
            except (KeyError, ValueError, TypeError, worktree.GitError) as e:
                logging.getLogger(__name__).warning("Branch-mode project %s left unchanged: %s", project.name, e)

    def _recover_starting(self) -> None:
        rows = self.store.db.execute("select id from tasks where checkout_state = 'starting' order by id").fetchall()
        for row in rows:
            task = self.store.get_task(row["id"])
            try:
                if branch_mode.on_branch(self._branch_checkout(task)):
                    self._set(task.id, checkout_state=None)
                    logging.getLogger(__name__).info("Linked interrupted checkout for %s", task.id)
                else:
                    self._set(task.id, repo=None, worktree_path=None, branch=None, base=None, base_branch=None,
                              checkout_state=None)
                    logging.getLogger(__name__).warning("Cleared interrupted checkout hold for %s", task.id)
            except (OSError, TypeError, worktree.GitError) as e:
                logging.getLogger(__name__).warning("Could not inspect interrupted checkout %s: %s", task.id, e)

    def _clear_lock(self, checkout, marker: str | None) -> str | None:
        """Clear a lock we created and the temporary files of this merge; the reason if a lock was left in place."""
        _, lock = branch_mode.index_lock_path(checkout.repo)
        branch_mode.sweep_temp_files(checkout.repo, marker)
        return None if branch_mode.clear_interrupted_lock(lock, marker) else branch_mode.lock_problem(lock, marker)

    def _marker_to_keep(self, task_id: str) -> str | None:
        """The merge marker, kept while a lock of ours is still in place, so a later recovery can still prove it ours."""
        task = self.store.get_task(task_id)
        try:
            _, lock = branch_mode.index_lock_path(self._branch_checkout(task).repo)
        except (worktree.GitError, OSError, TypeError):
            return task.merge_marker
        return task.merge_marker if branch_mode.lock_is_ours(lock, task.merge_marker) else None

    def _recover_merges(self) -> None:
        rows = self.store.db.execute("select id from tasks where merge_state = 'merging' order by id").fetchall()
        for row in rows:
            task = self.store.get_task(row["id"])
            try:
                checkout = self._branch_checkout(task)
                base = worktree._git(checkout.repo, "rev-parse", f"refs/heads/{checkout.original_branch}")
                if not branch_mode.is_ancestor(checkout.repo, task.merged_commit, base):
                    left = self._clear_lock(checkout, task.merge_marker)
                    self._set(task.id, merge_state="proposed", merged_commit=None, merged_tip=None,
                              merge_lock=None, merge_marker=self._marker_to_keep(task.id))
                    self._record(task.id, Notice(message="Interrupted merge did not advance the base; proposal restored."
                                                 + (f" {left}." if left else "")))
                    continue

                def mark_lock(task_id=task.id):
                    marker = branch_mode.new_nonce()
                    self._set(task_id, merge_lock=None, merge_marker=marker)
                    return marker

                recovered = False
                recovery_problem = None
                lock_left = None
                if base == task.merged_commit and branch_mode.on_branch(checkout):
                    try:
                        recovered = branch_mode.recover_merge(checkout, task.merged_commit, task.merged_tip,
                                                               task.merge_marker, mark_lock)
                    except (worktree.GitError, OSError) as e:
                        recovery_problem = str(e)
                        latest = self.store.get_task(task.id)
                        lock_left = self._clear_lock(checkout, latest.merge_marker)
                else:
                    lock_left = self._clear_lock(checkout, task.merge_marker)
                if recovered:
                    try:
                        leftovers, check_error = branch_mode.leftover_files(checkout.repo), None
                    except (worktree.GitError, OSError) as e:
                        leftovers, check_error = None, str(e)
                else:
                    leftovers = None
                    try:
                        head = worktree._git(checkout.repo, "symbolic-ref", "--short", "HEAD")
                    except worktree.GitError:
                        head = "detached"
                    state = f"base {checkout.original_branch} is {base[:12]}, HEAD is {head}"
                    check_error = (f"Merge recovered but checkout completion failed: {recovery_problem}; {state}"
                                   if recovery_problem else f"Merge recovered; {state}; checkout and refs left as found")
                    if lock_left and lock_left not in check_error:
                        check_error += f"; {lock_left}"
                    logging.getLogger(__name__).warning("%s: %s", task.id, check_error)
                self._set(task.id, merge_state="merged", merge_lock=None, merge_marker=self._marker_to_keep(task.id))
                self._record(task.id, kind="merged", data={"commit": task.merged_commit, "into": str(checkout.repo),
                                                            "forced": False, "automatic": False, "recovered": True,
                                                            "leftover_files": leftovers, "leftover_check_error": check_error})
            except (worktree.GitError, OSError, TypeError) as e:
                logging.getLogger(__name__).warning("Could not recover interrupted merge %s: %s", task.id, e)

    def _branch_notice(self, task_id: str, message: str) -> None:
        task = self.store.get_task(task_id)
        owner = task.review_of or task_id
        message += f"; dropping {owner} releases the project"
        self._record(task_id, Notice(message=message))
        if chief := self.coordinator():
            self.send(self.chat(chief.name).id, f"Branch-mode task {task_id}: {message}", sender="crewbots")

    def _cwd(self, task: Task, bot: Bot) -> Path:
        if task.review_of:
            reviewed = self.store.get_task(task.review_of)
            if reviewed.base_branch and task.workspace == reviewed.worktree_path:
                checkout = branch_mode.BranchCheckout(Path(reviewed.repo), reviewed.branch,
                                                       reviewed.base_branch, reviewed.base)
                if not branch_mode.on_branch(checkout):
                    raise NotAllowed("HEAD is not on the task branch; leaving the project unchanged")
                return Path(reviewed.worktree_path)
            if reviewed.worktree_path and task.workspace == reviewed.worktree_path:
                self._restore_worktree(reviewed)
            if self._in_review_copy(task) and not Path(task.workspace).exists():  # removed after an earlier turn
                if reviewed.worktree_path and not Path(reviewed.worktree_path).exists():
                    self._restore_worktree(reviewed)
                self._review_copy(reviewed, Path(task.workspace))
        if task.worktree_path:
            if task.base_branch:
                checkout = branch_mode.BranchCheckout(Path(task.repo), task.branch, task.base_branch, task.base)
                if not branch_mode.on_branch(checkout):
                    raise NotAllowed("HEAD is not on the task branch; leaving the project unchanged")
                return Path(task.worktree_path)
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
        if matching and matching[0].isolation == "branch":
            logging.getLogger(__name__).info("Project %s uses branch isolation; using a worktree until it is available",
                                             matching[0].name)
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
