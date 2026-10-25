"""Runs tasks: each one on its owner bot's backend, in its own worktree when it writes, with every event stored and
published as it happens.

A follow-up message to a task resumes the same backend session. Messages that arrive while the bot is mid-run wait
and go in together as the next turn, so a task never has two runs at once.
"""

import asyncio
import contextlib
from collections.abc import Iterator, Mapping
from contextlib import aclosing
from pathlib import Path
from typing import Any

from crewbots import worktree
from crewbots.backends import BACKENDS, Access, Backend, RunSpec
from crewbots.crew import Bot, Crew
from crewbots.events import Done, Event, SessionStarted, Usage
from crewbots.store import Status, Store, Task

SUBSCRIBER_QUEUE = 10_000


class UnknownBot(KeyError):
    pass


class Bus:
    """Fan-out of live updates to whoever is watching (the app, `crewbots watch`).

    A subscriber that falls too far behind is dropped rather than slowing the crew; it catches up from the store.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def publish(self, message: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                self._subscribers.discard(queue)

    @contextlib.contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(SUBSCRIBER_QUEUE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)


class Runner:
    def __init__(
        self,
        store: Store,
        crew: Crew,
        backends: Mapping[str, Backend] | None = None,
        *,
        max_parallel: int = 4,
        worktree_root: Path = worktree.DEFAULT_ROOT,
    ):
        self.store = store
        self.crew = crew
        self.backends = dict(backends) if backends else {name: cls() for name, cls in BACKENDS.items()}
        self.worktree_root = worktree_root
        self.bus = Bus()
        self._slots = asyncio.Semaphore(max_parallel)
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._waiting: dict[str, list[str]] = {}

    def submit(self, bot: str, goal: str, *, created_by: str = "user", parent_id: str | None = None,
               workspace: str | None = None) -> Task:
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        task = self.store.create_task(bot, goal, created_by=created_by, parent_id=parent_id, workspace=workspace)
        self.bus.publish({"type": "task", "task": task.to_json()})
        self._message(task.id, created_by, goal)
        self._start(task.id, goal)
        return task

    def send(self, task_id: str, text: str, sender: str = "user") -> Task:
        task = self.store.get_task(task_id)
        self._message(task_id, sender, text)
        if task_id in self._runs:
            self._waiting.setdefault(task_id, []).append(text)
            return task
        task = self._set(task_id, status=Status.QUEUED, error=None)
        self._start(task_id, text)
        return task

    async def cancel(self, task_id: str) -> Task:
        self.store.get_task(task_id)
        self._waiting.pop(task_id, None)
        if run := self._runs.get(task_id):
            run.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run
        return self._set(task_id, status=Status.CANCELLED)

    async def shutdown(self) -> None:
        """Stops every run. Their tasks are marked interrupted, so a follow-up message picks them up again."""
        runs = dict(self._runs)
        self._waiting.clear()
        for run in runs.values():
            run.cancel()
        await asyncio.gather(*runs.values(), return_exceptions=True)
        for task_id in runs:
            self._set(task_id, status=Status.INTERRUPTED, error="the daemon stopped during this task")

    def running(self) -> list[str]:
        return list(self._runs)

    def changes(self, task_id: str) -> str | None:
        """The task's diff against where its worktree started, or None if it has no worktree."""
        task = self.store.get_task(task_id)
        if not task.worktree_path:
            return None
        wt = worktree.Worktree(repo=Path(task.repo), path=Path(task.worktree_path), branch=task.branch, base=task.base)
        return worktree.changes(wt)

    def _start(self, task_id: str, prompt: str) -> None:
        self._runs[task_id] = asyncio.create_task(self._turns(task_id, prompt), name=f"task {task_id}")

    async def _turns(self, task_id: str, prompt: str | None) -> None:
        try:
            while prompt is not None:
                await self._run(task_id, prompt)
                waiting = self._waiting.pop(task_id, None)
                prompt = "\n\n".join(waiting) if waiting else None
        finally:
            self._runs.pop(task_id, None)

    async def _run(self, task_id: str, prompt: str) -> None:
        async with self._slots:
            task = self._set(task_id, status=Status.RUNNING, error=None)
            try:
                bot = self.crew.bots[task.bot]
                cwd = self._cwd(task, bot)
            except Exception as e:  # a bot removed from the crew, a missing workspace, a git failure
                self._record(task_id, Done(final_text="", is_error=True, error=f"crewbots: {e}"))
                self._set(task_id, status=Status.FAILED, error=str(e))
                return
            spec = RunSpec(
                prompt=prompt, cwd=cwd, access=bot.access, model=bot.model, system_prompt=self.crew.system_prompt(bot),
                resume_session_id=task.session_id, mcp_servers=bot.mcp, timeout_s=bot.timeout_s,
            )
            done: Done | None = None
            try:
                async with aclosing(self.backends[bot.backend].run(spec)) as stream:
                    async for event in stream:
                        self._record(task_id, event)
                        match event:
                            case SessionStarted(session_id=sid):
                                self.store.update_task(task_id, session_id=sid)
                            case Usage():
                                self.store.add_usage(task_id, event.input_tokens, event.output_tokens, event.cost_usd)
                            case Done():
                                done = event
            except asyncio.CancelledError:
                self._set(task_id, status=Status.CANCELLED)
                raise
            except Exception as e:
                done = Done(final_text="", is_error=True, error=f"crewbots: {e}")
                self._record(task_id, done)
            if done is None or done.is_error:
                self._set(task_id, status=Status.FAILED, result=done.final_text if done else None,
                          error=done.error if done else "the run ended without a result")
            else:
                # Not idle yet if messages are waiting: the next turn starts right away, and watchers should not stop.
                next_status = Status.QUEUED if self._waiting.get(task_id) else Status.IDLE
                self._set(task_id, status=next_status, result=done.final_text)

    def _cwd(self, task: Task, bot: Bot) -> Path:
        if task.worktree_path:
            return Path(task.worktree_path)
        workspace = Path(task.workspace).expanduser() if task.workspace else bot.workspace or self.crew.home
        if not workspace.is_dir():
            raise FileNotFoundError(f"workspace {workspace} does not exist")
        if bot.access is Access.READ_ONLY or not bot.worktree or not worktree.is_repo(workspace):
            return workspace
        wt = worktree.create(workspace, task.id, root=self.worktree_root)
        self.store.update_task(task.id, repo=str(wt.repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base)
        self._record(task.id, kind="worktree", data={"path": str(wt.path), "branch": wt.branch, "base": wt.base})
        return wt.path

    def _message(self, task_id: str, sender: str, text: str) -> None:
        self._record(task_id, kind="message", data={"from": sender, "text": text})

    def _record(self, task_id: str, event: Event | None = None, *, kind: str | None = None,
                data: dict[str, Any] | None = None) -> None:
        stored = self.store.append_event(task_id, event, kind=kind, data=data)
        self.bus.publish({"type": "event", "event": stored.to_json()})

    def _set(self, task_id: str, **fields: Any) -> Task:
        task = self.store.update_task(task_id, **fields)
        self.bus.publish({"type": "task", "task": task.to_json()})
        return task
