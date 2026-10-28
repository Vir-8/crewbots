"""Tasks: starting, reading, messaging, stopping, releasing and merging them, and answering what they ask to be
allowed."""

import asyncio

from fastapi import APIRouter, HTTPException, Query

from crewbots.api.context import Context
from crewbots.api.schemas import (
    ChangesOut,
    ChildOut,
    EventOut,
    MergeRequest,
    MergeResultOut,
    Message,
    NewTask,
    PermissionAnswer,
    PermissionOut,
    ReviewStateOut,
    TaskOut,
    TaskStatus,
    WithdrawalReason,
    WithdrawalResultOut,
)
from crewbots.store import Status


def routes(c: Context) -> APIRouter:
    runner, store = c.runner, c.store
    task_or_404, attached = c.task_or_404, c.attached
    v1 = APIRouter()

    @v1.get("/tasks", response_model=list[TaskOut])
    async def tasks(status: Status | None = None, bot: str | None = None, limit: int = Query(100, le=1000)):
        return [t.to_json() for t in store.list_tasks(status=status, bot=bot, limit=limit)]

    @v1.post("/tasks", status_code=201, response_model=TaskOut)
    async def new_task(body: NewTask):
        return runner.submit(body.bot, body.goal, workspace=body.workspace).to_json()

    @v1.post("/worktrees/sweep")
    async def sweep_worktrees():
        runner._check_worktree_sweep()
        runner._worktree_sweep_done.clear()
        operation = asyncio.create_task(asyncio.to_thread(runner.sweep_worktrees))
        operation.add_done_callback(lambda _: runner._worktree_sweep_done.set())
        return await asyncio.shield(operation)

    @v1.post("/tasks/{task_id}/drop-worktree")
    async def drop_worktree(task_id: str):
        task_or_404(task_id)
        return runner.drop_worktree(task_id)

    @v1.get("/tasks/{task_id}", response_model=TaskOut)
    async def task(task_id: str):
        return task_or_404(task_id).to_json()

    @v1.get("/tasks/{task_id}/status", response_model=TaskStatus)
    async def task_status(task_id: str):
        task_or_404(task_id)
        return await runner.status(task_id)

    @v1.get("/tasks/{task_id}/events", response_model=list[EventOut])
    async def task_events(task_id: str, after: int = 0, limit: int = Query(1000, le=10_000)):
        task_or_404(task_id)
        return [e.to_json() for e in store.events(task_id, after=after, limit=limit)]

    @v1.post("/tasks/{task_id}/messages", response_model=TaskOut)
    async def message(task_id: str, body: Message):
        task_or_404(task_id)
        return runner.send(task_id, body.text, attachments=attached(body), question_id=body.question_id).to_json()

    @v1.post("/tasks/{task_id}/cancel", response_model=TaskOut)
    async def cancel(task_id: str):
        task_or_404(task_id)
        return (await runner.cancel(task_id)).to_json()

    @v1.get("/tasks/{task_id}/changes", response_model=ChangesOut)
    async def changes(task_id: str):
        task_or_404(task_id)
        return {"diff": runner.changes(task_id)}

    @v1.post("/tasks/{task_id}/release", response_model=TaskOut)
    async def release(task_id: str):
        """Lets a task held on a budget or plan limit go ahead, with the rest of its goal."""
        task_or_404(task_id)
        return runner.release(task_id).to_json()

    @v1.post("/permissions/{permission_id}", response_model=PermissionOut)
    async def answer_permission(permission_id: int, body: PermissionAnswer):
        """The user allows what a bot asked, or not. Only the user can: a bot's token doesn't reach this."""
        try:
            return runner.answer_permission(permission_id, body.allowed)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from None

    @v1.get("/tasks/{task_id}/children", response_model=list[ChildOut])
    async def task_children(task_id: str):
        task_or_404(task_id)
        return runner.children_of(task_id)

    @v1.get("/tasks/{task_id}/review", response_model=ReviewStateOut)
    async def task_review(task_id: str):
        task_or_404(task_id)
        return runner.review_state(task_id).to_json()

    @v1.post("/tasks/{task_id}/merge", response_model=MergeResultOut)
    async def merge(task_id: str, body: MergeRequest | None = None):
        task_or_404(task_id)
        task = runner.merge(task_id, force=bool(body and body.force))
        event = store.last_event(task_id, ("merged",))
        data = event.data if event and event.data.get("commit") == task.merged_commit else {}
        return task.to_json() | {"leftover_files": data.get("leftover_files"),
                                 "leftover_check_error": data.get("leftover_check_error")}

    @v1.post("/tasks/{task_id}/withdraw-merge", response_model=WithdrawalResultOut)
    async def withdraw_merge(task_id: str, body: WithdrawalReason):
        task_or_404(task_id)
        task = runner.withdraw_merge(task_id, body.reason)
        event = store.last_event(task_id, ("merge_withdrawn",))
        return task.to_json() | {"notice": event.data.get("notice") if event else None}

    return v1
