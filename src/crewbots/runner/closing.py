"""Closing work without merging it: a merge proposal withdrawn, a task dropped on purpose, a task's folder removed."""

from crewbots.runner.common import NotAllowed
from crewbots.store import Status, Task


class Closing:
    """The part of Runner that closes work that won't be merged, keeping its branch."""

    def withdraw_merge(self, task_id: str, reason: str, *, parent_id: str | None = None) -> Task:
        """Withdraw a proposal, preserving work on its branch."""
        self._check_worktree_sweep(task_id)
        if parent_id is not None:
            parent = self.store.get_task(parent_id)
            if not self.is_coordinator(parent.bot):
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
        self._cleanup_after_action(task)
        return task

    async def drop(self, task_id: str, reason: str, *, by_task: str) -> dict[str, str]:
        """Closes finished work on purpose, so it isn't left owed: a duplicate, a fix a later merge covered, a check with
        nothing to merge. Its folder goes and its branch stays, in case it's ever wanted. For the bot that handed it out,
        or the chief."""
        caller = self.store.get_task(by_task)
        try:
            task = self.store.get_task(task_id)
        except KeyError:
            raise NotAllowed(f"no task {task_id}") from None
        if not self.is_chief(caller.bot) and (
                not task.parent_id or self.store.get_task(task.parent_id).bot != caller.bot):
            raise NotAllowed(f"{task_id} wasn't handed out by you; only the chief drops other bots' work")
        if task.kind != "thread" or task.review_of:
            raise NotAllowed(f"{task_id} is a {'review' if task.review_of else task.kind}, not work to drop")
        if not reason.strip():
            raise NotAllowed("give a one-line reason for dropping it")
        if task.merge_state == "dropped":
            return {"task_id": task.id, "status": "already dropped"}
        if task.merge_state in ("merged", "merging"):
            raise NotAllowed(f"{task_id} is {task.merge_state}; there is nothing to drop")
        if task.merge_state == "proposed":
            raise NotAllowed(f"{task_id} is proposed for merging; withdraw the proposal first (withdraw_merge)")
        if task.id in self._runs or any(r.active or r.id in self._runs for r in self.store.reviewers_of(task.id)):
            raise NotAllowed(f"{task_id} or a review of it is still working; message it to stop, or wait for it")
        if task.active or task.status in (Status.HELD, Status.NEEDS_INPUT, Status.INTERRUPTED):  # nor picked up again
            task = await self.cancel(task_id)
        self._mark_dropped(task, caller.bot, reason)
        return self._drop_checkout(self.store.get_task(task_id))

    def _mark_dropped(self, task: Task, by: str, reason: str) -> None:
        self._set(task.id, merge_state="dropped")
        self._record(task.id, kind="dropped", data={"by": by, "reason": " ".join(reason.split())[:300]})

    def _drop_checkout(self, task: Task) -> dict[str, str]:
        """A dropped task's folder, removed with its branch kept; work it hadn't committed is saved to the branch."""
        if not task.worktree_path:
            return {"task_id": task.id, "status": "dropped", "reason": "it had no folder"}
        try:
            result = self.drop_worktree(task.id)
        except NotAllowed as e:
            return {"task_id": task.id, "status": "dropped", "reason": f"its folder was kept: {e}"}
        return {**result, "status": "dropped",
                "reason": f"folder {result.get('status')}; branch {task.branch} kept" if task.branch else result.get(
                    "reason", "")}

    def drop_worktree(self, task_id: str) -> dict[str, str]:
        self._check_worktree_sweep(task_id)
        task = self.store.get_task(task_id)
        if task.merge_state == "proposed":
            raise NotAllowed("withdraw the open merge proposal first")
        return self._cleanup_worktree(task)
