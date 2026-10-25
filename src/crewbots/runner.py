"""Runs tasks: each one on its owner bot's backend, in its own worktree when it writes, with every event stored and
published as it happens.

A follow-up message to a task resumes the same backend session. Messages that arrive while the bot is mid-run wait
and go in together as the next turn, so a task never has two runs at once.

Delegation: a coordinator bot creates child tasks through the crewbots MCP tools, then ends its turn. When none of its
children are still running, their handoffs and results go back to it as one message, which starts its next turn. It
never waits or polls. Each run gets a token that lets its MCP server act for that task only, and only while it runs.
"""

import asyncio
import contextlib
import secrets
import sys
from collections.abc import Iterator, Mapping
from contextlib import aclosing
from pathlib import Path
from typing import Any

from crewbots import worktree
from crewbots.backends import BACKENDS, Access, Backend, McpServer, RunSpec
from crewbots.crew import Bot, Crew
from crewbots.events import Done, Event, SessionStarted, Usage
from crewbots.store import Handoff, Status, Store, Task

SUBSCRIBER_QUEUE = 10_000
MAX_CHILDREN = 12  # a stop condition: a coordinator that keeps delegating is looping, not managing
REPORT_RESULT_CHARS = 3000
MCP_SERVER_NAME = "crewbots"
MCP_COMMAND = (sys.executable, "-m", "crewbots.mcp_server")


class UnknownBot(KeyError):
    pass


class NotAllowed(PermissionError):
    """A bot asked for something its role or the crew's rules don't allow. The message goes back to the bot."""


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
        max_children: int = MAX_CHILDREN,
        worktree_root: Path = worktree.DEFAULT_ROOT,
        api_url: str | None = None,  # where bots' MCP servers reach the daemon; None runs bots without crew tools
        mcp_command: tuple[str, ...] = MCP_COMMAND,
    ):
        self.store = store
        self.crew = crew
        self.backends = dict(backends) if backends else {name: cls() for name, cls in BACKENDS.items()}
        self.worktree_root = worktree_root
        self.max_children = max_children
        self.api_url = api_url
        self.mcp_command = mcp_command
        self.bus = Bus()
        self._slots = asyncio.Semaphore(max_parallel)
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._waiting: dict[str, list[str]] = {}
        self._tokens: dict[str, str] = {}  # run token → task id, for as long as that run lasts
        self._escalated: set[str] = set()

    # Commands from the user (and, through `delegate` and `message_child`, from coordinators).

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
        if task.reported:
            self.store.update_task(task_id, reported=False)  # its next outcome goes back to the parent too
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
        task = self._set(task_id, status=Status.CANCELLED)
        self._report_up(task_id)
        return task

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

    # What a bot can do through its crewbots MCP server. `task_id` is always the caller's own task, from its token.

    def task_for_token(self, token: str) -> Task | None:
        for known, task_id in self._tokens.items():
            if secrets.compare_digest(known, token):
                return self.store.get_task(task_id)
        return None

    def delegate(self, parent_id: str, bot: str, goal: str, *, workspace: str | None = None,
                 review_of: str | None = None) -> Task:
        parent = self.store.get_task(parent_id)
        if not self.crew.bots[parent.bot].can_delegate:
            raise NotAllowed(f"{parent.bot} cannot delegate; only coordinators assign work (team/protocol.md)")
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        if self.crew.bots[bot].can_delegate:
            raise NotAllowed(f"{bot} is a coordinator; delegate to the bot that will do the work")
        if len(self.store.children(parent_id)) >= self.max_children:
            raise NotAllowed(
                f"stop condition: this task already delegated {self.max_children} tasks. Report to the user "
                "with what you have and what is still open, instead of delegating more.")
        if review_of:
            work = self._own_child(parent_id, review_of)
            workspace = work.worktree_path or work.workspace
        return self.submit(bot, goal, created_by=parent.bot, parent_id=parent_id,
                           workspace=workspace or parent.workspace)

    def children_of(self, parent_id: str) -> list[dict[str, Any]]:
        return [self._summary(c, include_result=True) for c in self.store.children(parent_id)]

    def message_child(self, parent_id: str, task_id: str, text: str) -> Task:
        self._own_child(parent_id, task_id)
        return self.send(task_id, text, sender=self.store.get_task(parent_id).bot)

    def handoff(self, task_id: str, done: str, output: list[str], open: str, needs: str) -> Handoff:
        task = self.store.get_task(task_id)
        handoff = self.store.add_handoff(task_id, task.bot, done, output, open, needs)
        self._record(task_id, kind="handoff", data=handoff.to_json())
        return handoff

    def escalate(self, task_id: str, question: str) -> None:
        """Marks the task as waiting on a decision. Its parent hears at once; a top-level task waits for the user."""
        task = self.store.get_task(task_id)
        self._escalated.add(task_id)
        self._record(task_id, kind="escalation", data={"question": question})
        if task.parent_id:
            self.store.update_task(task_id, reported=True)
            self.send(task.parent_id, sender="crewbots", text=(
                f"{task.bot} escalated on task {task_id} ({task.title}):\n\n{question}\n\n"
                f"Answer with message(task_id={task_id!r}, text=...), or escalate it to the user if the decision "
                "is theirs."))

    # Running.

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
        self._report_up(task_id)

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
            token = secrets.token_urlsafe(32)
            self._tokens[token] = task_id
            spec = RunSpec(
                prompt=prompt, cwd=cwd, access=bot.access, model=bot.model,
                system_prompt=self.crew.system_prompt(bot, delegated_by=task.created_by if task.parent_id else None),
                resume_session_id=task.session_id, mcp_servers={**bot.mcp, **self._crew_tools(token, bot)},
                read_dirs=[self.crew.skills_dir] if self.crew.skills_for(bot) else [], timeout_s=bot.timeout_s,
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
            finally:
                self._tokens.pop(token, None)
            if done is None or done.is_error:
                self._escalated.discard(task_id)
                self._set(task_id, status=Status.FAILED, result=done.final_text if done else None,
                          error=done.error if done else "the run ended without a result")
            elif task_id in self._escalated:
                self._escalated.discard(task_id)
                self._set(task_id, status=Status.NEEDS_INPUT, result=done.final_text)
            else:
                # Not idle yet if messages are waiting: the next turn starts right away, and watchers should not stop.
                next_status = Status.QUEUED if self._waiting.get(task_id) else Status.IDLE
                self._set(task_id, status=next_status, result=done.final_text)

    def _crew_tools(self, token: str, bot: Bot) -> dict[str, McpServer]:
        if not self.api_url:
            return {}
        command, *args = self.mcp_command
        env = {"CREWBOTS_URL": self.api_url, "CREWBOTS_TOKEN": token, "CREWBOTS_DELEGATE": "1" if bot.can_delegate else "0"}
        return {MCP_SERVER_NAME: McpServer(command=command, args=list(args), env=env)}

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

    # Reporting back up.

    def _report_up(self, task_id: str) -> None:
        """Once none of a parent's children are running, sends it everything they finished since its last report."""
        task = self.store.get_task(task_id)
        if not task.parent_id or task.active or task_id in self._runs:
            return
        parent = self.store.get_task(task.parent_id)
        if parent.status is Status.CANCELLED:
            return
        siblings = self.store.children(task.parent_id)
        if any(s.active or s.id in self._runs for s in siblings):
            return
        finished = [s for s in siblings if not s.reported]
        if not finished:
            return
        for s in finished:
            self.store.update_task(s.id, reported=True)
        sections = "\n\n".join(_report_section(s, self.store.latest_handoff(s.id)) for s in finished)
        self.send(task.parent_id, sender="crewbots", text=(
            f"{len(finished)} delegated task(s) finished, and none are still running.\n\n{sections}"))

    def _own_child(self, parent_id: str, task_id: str) -> Task:
        try:
            child = self.store.get_task(task_id)
        except KeyError:
            raise NotAllowed(f"no task {task_id}") from None
        if child.parent_id != parent_id:
            raise NotAllowed(f"task {task_id} was not delegated by this task")
        return child

    def _summary(self, task: Task, include_result: bool = False) -> dict[str, Any]:
        summary: dict[str, Any] = {"id": task.id, "bot": task.bot, "title": task.title, "status": task.status}
        if task.branch:
            summary |= {"worktree": task.worktree_path, "branch": task.branch}
        if include_result:
            handoff = self.store.latest_handoff(task.id)
            summary |= {"handoff": handoff.to_json() if handoff else None, "result": _clip(task.result),
                        "error": task.error}
        return summary

    # Bookkeeping.

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


def _report_section(task: Task, handoff: Handoff | None) -> str:
    lines = [f"## {task.id} · {task.bot} · {task.status}", f"Task: {task.title}"]
    if task.branch:
        lines.append(f"Worktree: {task.worktree_path} (branch {task.branch})")
    if task.error:
        lines.append(f"Error: {task.error}")
    if handoff:
        lines += ["HANDOFF", f"Done: {handoff.done}", f"Output: {', '.join(handoff.output) or '(none)'}",
                  f"Open: {handoff.open}", f"Needs: {handoff.needs}"]
    if task.result:
        lines += ["Final message:", _clip(task.result) or ""]
    return "\n".join(lines)


def _clip(text: str | None) -> str | None:
    if text is None or len(text) <= REPORT_RESULT_CHARS:
        return text
    return text[:REPORT_RESULT_CHARS] + f"\n… ({len(text) - REPORT_RESULT_CHARS} more characters; ask the task for them)"
