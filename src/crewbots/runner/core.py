"""Runs tasks on their owner's backend and publishes events as they happen.

A follow-up resumes the same session. Messages arriving mid-run wait for the next turn.

Each active task keeps its CLI running between turns. A session closes when idle, cancelled or stopped; otherwise a
backends without sessions use one process per turn, resuming from the stored session id.

Delegation: coordinators create child tasks through MCP tools, then end their turns. Once all children finish, their
handoffs and results go back as one message. The coordinator
never waits or polls. Each run gets a token that lets its MCP server act for that task only, and only while it runs.

Chats and rooms: each bot has one ongoing chat with the user, which never ends; real work goes into threads opened
from it. A room is a group chat. A message there wakes the bots it @mentions, or the room's lead if it mentions no
one; a bot's reply is posted back to the room and wakes whoever it mentions in turn. Bots can stay silent when they
have nothing to add, and rooms can optionally limit replies.

Budgets: before a turn starts, the runner checks the bot's budget, the budget of the routine run it belongs to, and
the plan's usage limit. If any is spent, the task is held: the turn waits (holding no run slot), and goes ahead by
itself once there's room again, or at once when the user releases it. A turn already running is never cut short."""

import asyncio
import contextlib
import os
import secrets
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import aclosing
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from crewbots import attachments as attachments_module
from crewbots import branch_mode, prompts
from crewbots.attention import Attention
from crewbots.backends import BACKENDS, Access, Backend, McpServer, RunSpec
from crewbots.browser import Browser
from crewbots.capacity import Capacity
from crewbots.crew import NO_REPLY, Bot, Crew
from crewbots.crew_tools import CrewTools
from crewbots.events import (
    Compacted,
    Done,
    Event,
    Notice,
    RateLimit,
    SessionStarted,
    Text,
    ToolCall,
    ToolResult,
    Usage,
)
from crewbots.reviews import review_prompt, reviewer_bot
from crewbots.runner.branches import Branches
from crewbots.runner.checkins import Checkins
from crewbots.runner.common import (
    MCP_COMMAND,
    MCP_SERVER_NAME,
    Bus,
    NotAllowed,
    OpenSession,
    UnknownBot,
    _duration,
    _usage_data,
)
from crewbots.runner.delegation import MAX_CHILDREN, MAX_DEPTH, MAX_OUT, Delegation
from crewbots.runner.dreams import Dreams
from crewbots.runner.files import Files
from crewbots.runner.holds import Holds
from crewbots.runner.permissions import Permissions
from crewbots.runner.retries import Retries
from crewbots.runner.reviews import Reviewing
from crewbots.runner.rooms import Rooms
from crewbots.runner.sessions import Sessions
from crewbots.runner.wakes import Wakes
from crewbots.runner.welcome import Welcome
from crewbots.runner.work import Work
from crewbots.store import Status, Store, Task

IDLE_TIMEOUT_S = 600.0  # an idle session is closed after this; its next message resumes it from the stored id
MAX_SESSIONS = 8  # open sessions at once; past this the least recently used idle one is closed
ROLLOVER_PERCENT = 70.0  # past this much of its context window, a conversation is flushed to memory and compacted
COLD_S = 3600.0  # the prompt cache's life: a conversation idle this long reads back in at full price
FLUSH_IDLE_S = 3000.0  # 50 minutes into idleness, while the cache is warm, a big conversation sets down its state
# A turn that failed for a passing reason (the backend says which) is tried again after each of these waits, then
# left failed. The CLIs have already retried the API call itself a few times by then, so these are longer.
RETRY_WAITS_S = (30.0, 120.0, 300.0)
STALL_S = 1200.0  # no event at all from a turn for this long: it's stuck, so it's stopped and tried again


class Runner(Branches, Retries, Sessions, Rooms, Delegation, Reviewing, Work, Files, Holds, Dreams, Checkins, Wakes,
             Welcome, Permissions):
    """Runs the crew's tasks. Its parts are by area: sessions, rooms, delegation, reviews, work, files bots show the
    user, holds, dreams (memory consolidated overnight), check-ins, the chief's own looks around (wakes), starting a
    crew off (welcome), and what bots ask the user to allow (permissions)."""
    def __init__(
        self,
        store: Store,
        crew: Crew,
        backends: Mapping[str, Backend] | None = None,
        *,
        max_parallel: int = 4,
        max_children: int = MAX_CHILDREN,
        max_depth: int = MAX_DEPTH,  # delegations in a row
        max_out: int = MAX_OUT,  # unfinished tasks one bot may have delegated at once
        worktree_root: Path | None = None,  # default: ~/.crewbots/worktrees
        api_url: str | None = None,  # where bots' MCP servers reach the daemon; None runs bots without crew tools
        mcp_command: tuple[str, ...] = MCP_COMMAND,
        sessions: bool = True,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
        max_sessions: int = MAX_SESSIONS,
        keep_awake: tuple[str, ...] | None = None,  # a command that stops the Mac sleeping while it runs
        rollover_percent: float | None = ROLLOVER_PERCENT,
        cold_s: float = COLD_S,
        flush_idle_s: float = FLUSH_IDLE_S,
        retry_waits_s: tuple[float, ...] | None = None,
        stall_s: float | None = None,
    ):
        self.browser = Browser()
        self.store = store
        self.crew = crew
        # Preserve the old selection once, before any bots can be added or reordered.
        legacy = next((b for b in crew.bots.values() if b.can_delegate), None)
        store.set_chief(legacy.name if legacy else "", only_if_unset=True)
        store.set_coordinators([b.name for b in crew.bots.values() if b.can_delegate], only_if_unset=True)
        self.backends = dict(backends) if backends else {name: cls() for name, cls in BACKENDS.items()}
        self.worktree_root = worktree_root
        self.max_children = max_children
        self.max_depth = max_depth
        self.max_out = max_out
        self.api_url = api_url
        self.mcp_command = mcp_command
        self.bus = Bus()
        self._slots = asyncio.Semaphore(max_parallel)
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._attempts: dict[str, dict[str, Any]] = {}
        self._cancelling: set[str] = set()
        self._held_notices: dict[str, list[str]] = {}  # state notices for a stopped chat, until the user writes next
        self._cancel_messages: dict[str, list[tuple[str, str, list[dict[str, Any]] | None, bool, bool]]] = {}
        self._seat_turn_seen: dict[str, int] = {}
        self._seat_queue_floor: dict[str, int] = {}
        self._seat_dispatch: dict[str, int] = {}
        self._waiting: dict[str, list[str]] = {}
        self._tokens: dict[str, str] = {}  # run or session token → task id, while that run or session lasts
        self._escalated: set[str] = set()
        self.use_sessions = sessions
        self.idle_timeout_s = idle_timeout_s
        self.max_sessions = max_sessions
        self._sessions: dict[str, OpenSession] = {}
        self._context_seen: dict[str, float] = {}  # task id → how full its context was, when last measured
        self._reaper: asyncio.Task[None] | None = None
        self.keep_awake = keep_awake
        self.rollover_percent = rollover_percent
        self.cold_s = cold_s
        self.flush_idle_s = flush_idle_s
        self._turn_ended: dict[str, float] = {}  # task id → when its last turn ended (time.time())
        self._removed_worktrees: set[str] = set()  # close stale sessions before reopening a removed checkout
        self._worktree_sweep_done = asyncio.Event()
        self._worktree_sweep_done.set()
        self._flushed: set[str] = set()  # tasks whose bot set down where things stand since their last turn
        self._dreaming: set[str] = set()  # bots whose memory is being consolidated now
        self._waking: set[str] = set()  # chats looking around on their own (wakes.py): what they say is said unasked
        self.capacity = Capacity(self)  # whether background work may run now
        self.attention = Attention(self)  # when what the crew raises on its own reaches the user
        self._started = time.time()
        self.retry_waits_s = RETRY_WAITS_S if retry_waits_s is None else retry_waits_s
        self.stall_s = STALL_S if stall_s is None else stall_s
        self._awake: subprocess.Popen[bytes] | None = None
        self._resumed = asyncio.Event()  # cleared while the crew is paused: no new turn starts
        self._resumed.set()
        self.restarting = False  # the daemon restarts once no turn is running; meanwhile none starts
        self._user_paused = False  # what pausing was before a restart was asked for
        self._held: set[str] = set()  # tasks whose next turn waits on a budget or a plan limit
        self._released: set[str] = set()  # top-level tasks the user let past their budgets, until the daemon stops
        self._recheck = asyncio.Event()  # set when a hold may be over: a release, or a reloaded crew
        self.limits: dict[str, dict[str, RateLimit]] = {}  # plan → window → the latest the CLI reported
        self._seat_depth: dict[str, int] = {}  # seat task → the depth of the room message that woke it
        self._owed: set[str] = set()  # seats with a pending user mention
        self._nudged: set[str] = set()  # seats with a pending reminder to answer the user
        self._shared_now: dict[str, set[Path]] = {}  # task → files it shared this turn, which its reply needn't show
        # A code review's tools (reading the repo on GitHub), given its task; None where it has your local copy.
        self.review_tools: Callable[[Task], Any] | None = None

    def submit(self, bot: str, goal: str, *, created_by: str = "user", parent_id: str | None = None,
               workspace: str | None = None, review_of: str | None = None, review_version: int | None = None,
               routine: str | None = None, budget: int | None = None, title: str | None = None,
               pr: str | None = None, kind: str = "thread", said_by: str | None = None) -> Task:
        """`said_by` is who its first message shows as, when that isn't who it was created by: "crewbots" keeps a
        long prompt of crewbots' own out of view."""
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        project = self._branch_project(bot, workspace, kind, review_of)
        task = self.store.create_task(bot, goal, created_by=created_by, parent_id=parent_id, workspace=workspace,
                                      review_of=review_of, review_version=review_version, routine=routine,
                                      budget=budget, title=title, pr=pr, kind=kind)
        if project:
            task = self._start_branch(task, project)
        self.bus.publish({"type": "task", "task": task.to_json()})
        self._message(task.id, said_by or created_by, goal)
        if project:
            self._message(task.id, "crewbots", f"Your {project.name} folder is on the crew's branch until this task "
                          "merges; anything you save there becomes part of this task.")
        self._start(task.id, goal)
        return task

    def chat(self, bot: str) -> Task:
        """The bot's chat, made the first time it's needed."""
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        existing = self.store.chat_of(bot)
        if existing:
            return existing
        task = self.store.create_task(bot, f"Chat with {bot}", kind="chat", status=Status.IDLE)
        self.bus.publish({"type": "task", "task": task.to_json()})
        return task

    def say(self, bot: str, text: str, sender: str = "user", attachments: list[dict[str, Any]] | None = None) -> Task:
        return self.send(self.chat(bot).id, text, sender=sender, attachments=attachments)

    def send(self, task_id: str, text: str, sender: str = "user",
             attachments: list[dict[str, Any]] | None = None, *, _record_message: bool = True,
             question_id: int | None = None) -> Task:
        """A message to a task. Attachments are shown with it, and listed, with where they are, in what the bot gets.
        One from a teammate reaches the bot marked as a teammate's, so it isn't taken for the user's word."""
        task = self.store.get_task(task_id)
        if task_id in self._cancelling:
            if sender == "crewbots" or sender.startswith("routine:"):
                self._cancel_messages.setdefault(task_id, []).append(
                    (text, sender, attachments, _record_message, task_id in self._waking))
                return task
            raise NotAllowed("This task is being cancelled. Please try again after cancellation finishes.")
        if sender == "user" and task.kind == "seat":
            self._check_seat_answer(task, question_id)
        elif question_id is not None:
            raise NotAllowed("This question does not belong to a room seat.")
        answered_escalation, nudge_decision = self._answer_decision(task, text) if sender == "user" else (False, False)
        if _record_message:
            self._message(task_id, sender, text, attachments)
        if sender == "user":
            if answered_escalation:
                if task.kind == "seat" and task.room_id:
                    asked = self.store.last_event(task_id, ("escalation",))
                    self._record(self.chat(task.bot).id, kind="seat_escalation_answer", data={
                        "question_id": asked.id, "text": text})
                self._clear_room_need(task_id)
            self._refresh_working_state(task.bot)
            self.attention.user_wrote = time.time()  # mid-conversation: questions wait for a break
        if sender == "user" and (held := self._held_notices.pop(task_id, None)):
            text = "\n\n".join([*dict.fromkeys(held), text])
        if nudge_decision:
            text += "\n\nRecord the decision in your working state, distilled, with no private detail."
        if sender in self.crew.bots and sender != task.bot:
            text = prompts.text("from-teammate", self.crew.home, teammate=sender, text=text)
        text += attachments_module.listing(attachments or [])
        getting_code = task.parent_id is None and task.status == Status.QUEUED and not task.workspace
        if task.kind == "review" and task_id not in self._runs and sender != "crewbots" and (getting_code or any(
                c.active or c.id in self._runs for c in self.store.children(task_id))):
            self._waiting.setdefault(task_id, []).append(text)  # the lead answers once it has the reviewers' findings
            return task
        if task.reported:
            self.store.update_task(task_id, reported=False)  # its next outcome goes back to the parent too
        if task_id in self._runs:
            return self._queue_during_run(task, text)
        task = self._set(task_id, status=Status.QUEUED, error=None)
        self._start(task_id, text)
        return task

    async def cancel(self, task_id: str, *, _begun: bool = False) -> Task:
        if not _begun and task_id in self._cancelling:
            return self.store.get_task(task_id)
        if not _begun:
            self._begin_cancel(task_id)
        try:
            if run := self._runs.get(task_id):
                with contextlib.suppress(asyncio.CancelledError):
                    await run
                if self._runs.get(task_id) is run:  # a task cancelled before its coroutine started has no finally
                    self._runs.pop(task_id, None)
                    self._seat_turn_seen.pop(task_id, None)
                    self._waking.discard(task_id)
                    self._stay_awake()
            opened = self._sessions.get(task_id)
            if not (opened and opened.session.alive and getattr(opened.session, "interruptible", False)):
                await self._close_session(task_id)  # an interruptible session stopped its turn and stays warm
            await self.browser.close_task(task_id)
            task = self._set(task_id, status=Status.CANCELLED)
            if task.kind == "review" and task.parent_id is None:  # stopping a review stops its reviewers
                for reviewer in self.store.children(task_id):
                    if reviewer.active or reviewer.id in self._runs:
                        await self.cancel(reviewer.id)
            self._report_up(task_id)
            return task
        finally:
            if not _begun:
                self._cancelling.discard(task_id)
                for text, sender, attachments, record, waking in self._cancel_messages.pop(task_id, []):
                    if waking:
                        self._waking.add(task_id)
                    self.send(task_id, text, sender, attachments, _record_message=record)

    async def shutdown(self) -> None:
        """Stops every run. Their tasks are marked interrupted, so a follow-up message picks them up again."""
        runs = dict(self._runs)
        self._waiting.clear()
        for run in runs.values():
            run.cancel()
        await asyncio.gather(*runs.values(), return_exceptions=True)
        if self._reaper:
            self._reaper.cancel()
        await self.browser.close()
        self._resumed.set()
        for task_id in list(self._sessions):
            await self._close_session(task_id)
        for task_id in runs:
            self._interrupt_after_shutdown(task_id)

    def resume_interrupted(self) -> list[str]:
        """On start: each turn the last stop cut off, or that was waiting its turn, carries on once. A bot that had
        the turn is told it was interrupted and to pick up where it left off; one that hadn't gets it as it was. A
        held one is held again until there's room. Returns the tasks carried on."""
        self._recover_branch_projects()
        resumed = []
        for turn in self.store.interrupted_turns():
            task = self.store.get_task(turn["task_id"])
            self.store.clear_pending(task.id)
            if task.kind == "review" or task.bot not in self.crew.bots:
                continue  # a code review is restarted by the user, as it always was
            if turn["resumed"]:
                self._record(task.id, kind="restarted", data={"again": True})
                continue
            prompt = prompts.text("resume", self.crew.home, request=turn["prompt"]) if turn["started"] else turn["prompt"]
            prompt = "\n\n".join([prompt, *turn["waiting"]])
            self.store.set_pending(task.id, turn["prompt"], resumed=True)
            if turn["started"]:
                self._record(task.id, kind="restarted", data={"again": False})
            self._set(task.id, status=Status.QUEUED, error=None)
            self._start(task.id, prompt, resumed=True)
            resumed.append(task.id)
        return resumed

    def running(self) -> list[str]:
        return list(self._runs)

    def bot_for(self, name: str) -> Bot | None:
        """A crew bot, or the reviewer agent a code review runs as (it isn't one of the crew)."""
        return self.crew.bots.get(name) or reviewer_bot(name, self.crew.home)

    def note(self, task_id: str, message: str) -> None:
        """A line in a task's conversation from crewbots itself, such as what a review is waiting on."""
        self._record(task_id, Notice(message=message))

    def fail(self, task_id: str, error: str) -> Task:
        """A task that can't go ahead, for a reason found outside a run (a review whose code couldn't be fetched)."""
        self._waiting.pop(task_id, None)
        self._record(task_id, Done(final_text="", is_error=True, error=error))
        return self._set(task_id, status=Status.FAILED, error=error)

    def queued(self, task_id: str) -> list[str]:
        """Messages waiting for the task's current turn to finish."""
        return [*self._waiting.get(task_id, []), *(item[0] for item in self._cancel_messages.get(task_id, []))]

    def state(self) -> dict[str, Any]:
        limits = [asdict(limit) for windows in self.limits.values() for limit in windows.values()]
        return {"paused": self.paused, "restarting": self.restarting, "running": len(self._runs) - len(self._held),
                "turns_running": self.turns_running(), "held": len(self._held), "sessions": len(self._sessions),
                "limits": [{k: v for k, v in x.items() if k != "kind"} for x in limits],
                "background": self.capacity.summary()}

    def user_busy(self) -> bool:
        """Whether any of the user's own work is running: anything but the crew's background work."""
        return any(not self._background(task_id) for task_id in self._runs if task_id not in self._held)

    def _background(self, task_id: str) -> bool:
        """Work the crew does for itself: started by crewbots (memory consolidation, check-ins), however deep."""
        return self.store.work_of(task_id).created_by == "crewbots"

    def reload(self, crew: Crew) -> None:
        """Takes up edited role cards (and their budgets). Running turns carry on as they started. Bots new to the crew
        join its own group chat."""
        added = set(crew.bots) - set(self.crew.bots)
        self.crew = crew
        self._join_crew_room(added)
        self._wake_held()
        self.bus.publish({"type": "crew", "crew": self.state()})
        self.bus.publish({"type": "bots"})  # names and setups the app shows may have changed

    def task_for_token(self, token: str) -> Task | None:
        for known, task_id in self._tokens.items():
            if secrets.compare_digest(known, token):
                return self.store.get_task(task_id)
        return None

    def _start(self, task_id: str, prompt: str, resumed: bool = False) -> None:
        if task_id in self._cancelling:
            raise NotAllowed("This task is being cancelled. Please try again after cancellation finishes.")
        owed, nudged = task_id in self._owed, task_id in self._nudged
        self._owed.discard(task_id)
        self._nudged.discard(task_id)
        task = self.store.get_task(task_id)
        seen = self.store.seen(f"seat:{task_id}") if task.kind == "seat" else 0
        if task.kind == "seat":
            self._seat_turn_seen[task_id] = seen
        self._runs[task_id] = asyncio.create_task(self._turns(task_id, prompt, resumed, owed, nudged, seen),
                                                  name=f"task {task_id}")
        self._stay_awake()
        if self.use_sessions and (self._reaper is None or self._reaper.done()):
            self._reaper = asyncio.create_task(self._reap_idle(), name="session reaper")

    async def _turns(self, task_id: str, prompt: str | None, resumed: bool = False,
                     owed: bool = False, nudged: bool = False, seat_seen: int = 0) -> None:
        settled = False
        try:
            while prompt is not None:
                self._seat_turn_seen[task_id] = seat_seen
                if not resumed:  # a turn carried on after a restart keeps the request it carries on
                    self.store.set_pending(task_id, prompt)
                resumed = False
                await self._tries(task_id, prompt, owed, nudged)
                waiting = self._waiting.pop(task_id, None)
                prompt = "\n\n".join(waiting) if waiting else None
                seat_seen = self.store.seen(f"seat:{task_id}")
                owed, nudged = task_id in self._owed, task_id in self._nudged
                self._owed.discard(task_id)
                self._nudged.discard(task_id)
            settled = True
        finally:
            if settled:  # stopped by the daemon stopping, it's kept for the next start to carry on
                self.store.clear_pending(task_id)
                self._seat_queue_floor.pop(task_id, None)
            self._runs.pop(task_id, None)
            self._seat_turn_seen.pop(task_id, None)
            self._waking.discard(task_id)
            self._stay_awake()
        self._report_up(task_id)
        task = self.store.get_task(task_id)
        if settled and task.kind == "thread" and not task.active and not self._background(task_id):
            self.happened("thread_done", task_id, f"{task.bot}'s thread {task.title!r} is {task.status.value}")

    async def _run(self, task_id: str, prompt: str, owed: bool = False, nudged: bool = False) -> Done | None:
        """One turn. Returns its Done if it failed, for `_tries` to settle; None once the task is settled."""
        async with self._turn_slot(task_id):
            if (task := self.store.get_task(task_id)).worktree_path or task.review_of:
                await self._worktree_sweep_done.wait()
            self._attempts.get(task_id, {}).update(state="running")
            task = self._set(task_id, status=Status.RUNNING, error=None)
            self.store.pending_started(task_id)
            try:
                bot = self.bot_for(task.bot)
                if bot is None:
                    raise KeyError(f"no bot named {task.bot!r} in the crew")
                if task_id in self._removed_worktrees:
                    await self._close_session(task_id)
                    self._removed_worktrees.discard(task_id)
                cwd = self._cwd(task, bot)
                branch_task = self.store.get_task(task.review_of) if task.review_of else task
                branch_start = branch_mode.turn_head(self._branch_checkout(branch_task)) if branch_task.base_branch else None
            except Exception as e:  # a bot removed from the crew, a missing workspace, a git failure
                self._record(task_id, Done(final_text="", is_error=True, error=f"crewbots: {e}"))
                self._failed(task_id, str(e))
                if (task.base_branch or task.review_of) and "HEAD is not on the task branch" in str(e):
                    self._branch_notice(task_id, str(e))
                return None
            backend = self.backends[bot.backend]
            if task.kind != "review":
                self._refresh_working_state(bot.name)
            task = await self._without_stale_session(task, bot)
            open_session = await self._session_for(task, bot, cwd, backend)
            prompt, primed = self._with_updates(task, bot, prompt)
            compacted = False
            token = open_session.token if open_session else secrets.token_urlsafe(32)
            self._tokens[token] = task_id
            done: Done | None = None
            stalled = False
            looked_at: dict[str, str] = {}
            started = time.time()  # a text file a reply names counts as made by it if it changed since
            self._shared_now[task_id] = set()
            branch_error: str | None = None

            async def watch_branch() -> None:
                nonlocal branch_error
                checkout = self._branch_checkout(branch_task)
                while True:
                    await asyncio.sleep(2)
                    if not branch_mode.turn_safe(checkout, branch_start):
                        branch_error = f"{branch_task.repo}: HEAD left {branch_task.branch} or its history was rewritten; stopped"
                        if run := self._runs.get(task_id):
                            run.cancel()
                        return

            watcher = asyncio.create_task(watch_branch()) if branch_task.base_branch else None
            try:
                if task.kind == "seat":
                    self._seat_dispatch[task_id] = self._seat_turn_seen.get(task_id, 0)
                if open_session:
                    stream = open_session.session.turn(prompt)
                else:
                    stream = backend.run(self._spec(task, bot, cwd, token, prompt, backend))
                if task_id in self._attempts:
                    self._attempts[task_id]["last_activity"] = time.monotonic()
                async with aclosing(stream) as stream:
                    while True:
                        try:
                            async with asyncio.timeout(self.stall_s):
                                event = await anext(stream)
                        except StopAsyncIteration:
                            break
                        except TimeoutError:
                            stalled = True
                            if task_id in self._attempts:
                                self._attempts[task_id]["state"] = "stalled"
                            done = Done(final_text="", is_error=True, retry="transient",
                                        error=f"nothing came back from {bot.backend} for {_duration(self.stall_s)}")
                            self._record(task_id, done)
                            break
                        if task_id in self._attempts:
                            self._attempts[task_id]["last_activity"] = time.monotonic()
                        if (task.kind == "seat" and not isinstance(event, (SessionStarted, Notice, RateLimit))
                                and not (isinstance(event, Done) and event.is_error)):
                            self._room_prompt_sent(task_id)
                        if isinstance(event, RateLimit):
                            self._note_limit(task_id, event)
                            continue
                        if isinstance(event, Usage):  # kept with what spent it, for the usage page
                            self._record(task_id, kind="usage", data=_usage_data(event, bot))
                        else:
                            if isinstance(event, ToolCall):  # what its result's images are called: the file read
                                looked_at[event.id] = str(event.input.get("file_path") or event.input.get("path") or "")
                            elif isinstance(event, ToolResult) and event.images:  # as files, not in the database
                                kept = await asyncio.to_thread(self._kept_images, event.images, looked_at.get(event.id))
                                event = replace(event, images=kept)
                            elif isinstance(event, Text):
                                if event.text.strip("`*_. ") == NO_REPLY:
                                    continue  # nothing to say: the chat shows nothing
                                named = self._mentioned(task, cwd, event.text, started)
                                if named or task_id in self._waking:
                                    self._record(task_id, kind="text", data={
                                        "text": event.text, **({"files": named} if named else {}),
                                        **({"checkin": True} if task_id in self._waking else {})})
                                    continue
                            self._record(task_id, event)
                        match event:
                            case SessionStarted(session_id=sid):
                                self.store.update_task(task_id, session_id=sid, session_backend=bot.backend)
                            case Usage():
                                self.store.add_usage(task_id, event.input_tokens, event.output_tokens, event.cost_usd)
                            case Compacted():  # the CLI compacted it on its own, mid-turn
                                compacted = True
                            case Done():
                                done = event
            except asyncio.CancelledError:
                if branch_error:
                    self.store.clear_pending(task_id)
                    self._set(task_id, status=Status.FAILED, error=branch_error)
                    self._branch_notice(task_id, branch_error)
                else:
                    self._set(task_id, status=Status.CANCELLED)
                raise
            except Exception as e:  # the backend broke: its CLI wouldn't start, or its protocol did
                done = Done(final_text="", is_error=True, error=f"crewbots: {e}", retry="transient")
                self._record(task_id, done)
            finally:
                if watcher:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                    checkout = self._branch_checkout(branch_task)
                    if not branch_error and not branch_mode.turn_safe(checkout, branch_start):
                        branch_error = f"{branch_task.repo}: HEAD left {branch_task.branch} or its history was rewritten; stopped"
                        self._branch_notice(task_id, branch_error)
                        done = Done(final_text="", is_error=True, error=branch_error)
                        self._record(task_id, done)
                self._seat_dispatch.pop(task_id, None)
                await self.browser.close_task(task_id)
                if open_session is None:
                    self._tokens.pop(token, None)
                elif (open_session.session.alive and not stalled and not branch_error
                      and open_session.coordinator == self.is_coordinator(task.bot)
                      and open_session.chief == self.is_chief(task.bot)):
                    open_session.last_used = asyncio.get_running_loop().time()
                else:
                    await self._close_session(task_id)  # it died, stalled, or was stopped mid-turn: the next turn resumes
            if done is None or done.is_error:
                self._escalated.discard(task_id)
                return done or Done(final_text="", is_error=True, error="the run ended without a result",
                                    retry="transient")
            if compacted:  # its next turn is told its instructions and its state again
                self.store.set_primed(task_id, {})
            elif primed:  # it has these now; a turn that failed hears them again
                self.store.set_primed(task_id, primed)
            self._turn_ended[task_id] = time.time()
            self._flushed.discard(task_id)
            if task_id in self._escalated:
                self._escalated.discard(task_id)
                self._set(task_id, status=Status.NEEDS_INPUT, result=done.final_text, turns=task.turns + 1)
            else:
                if task.kind == "seat" and task.room_id:
                    self._seat_replied(task, done.final_text, self._mentioned(task, cwd, done.final_text, started),
                                       owed, nudged)
                    self._seat_turn_finished(task, done.final_text)
                try:
                    await self._maybe_compact(task_id, open_session, bot)
                except NotAllowed as e:
                    return Done(final_text="", is_error=True, error=str(e))
                # Not idle yet if messages are waiting: the next turn starts right away, and watchers should not stop.
                next_status = Status.QUEUED if self._waiting.get(task_id) else Status.IDLE
                if task.kind != "seat" or self.store.get_task(task_id).status != next_status:
                    self._set(task_id, status=next_status, result=self._checked(task, done.final_text), turns=task.turns + 1)

    def _spec(self, task: Task, bot: Bot, cwd: Path, token: str, prompt: str, backend: Backend) -> RunSpec:
        if task.kind == "review":  # its own instructions and tools, and nothing of the crew's: no roster, no memory
            tools = self.review_tools(task) if self.review_tools and self.api_url else None
            in_process = tools is not None and getattr(backend, "in_process_tools", False)
            flags = ",".join(f for f, on in (("github", getattr(tools, "reads_github", False)),
                                             ("drafts", getattr(tools, "drafting", False))) if on)
            servers = {} if tools is None or in_process else self._crew_tools(token, bot, tools="review", flags=flags)
            # The lead puts the others' findings together.
            system = review_prompt("lead", self.crew.home) if task.parent_id is None else bot.instructions
            return RunSpec(prompt=prompt, cwd=cwd, access=bot.access, model=bot.model, system_prompt=system,
                           resume_session_id=task.session_id, timeout_s=bot.timeout_s, effort=bot.effort,
                           mcp_servers=servers, crew=tools if in_process else None, network=bot.network,
                           deny_read=self._state_denials(bot.name))
        # A backend that hosts tools in-process gets the crew tools as Python; any other, as an MCP server with a token.
        in_process = getattr(backend, "in_process_tools", False) and self.api_url is not None
        grants = self.store.grants(task.id)  # what the user allowed this task beyond its bot's role card
        proactive = task.created_by == "crewbots" and task.parent_id is not None  # started on its own, from a check-in
        return RunSpec(
            prompt=prompt, cwd=cwd, model=bot.model,
            on_prompt_sent=(lambda: self._room_prompt_sent(task.id)) if task.kind == "seat" else None,
            access=Access.READ_ONLY if task.kind == "ask" or (task.review_of and
                self.store.get_task(task.review_of).base_branch) else bot.access,
            system_prompt=self.crew.system_prompt(bot, delegated_by=task.created_by if task.parent_id else None,
                                                  coordinator=self.is_coordinator(bot.name), chief=self.is_chief(bot.name),
                                                  reviewing=self._review_brief(task), briefing=task.briefing,
                                                  routine=task.routine, setting=self._setting(task),
                                                  recent=self._recent(bot.name),
                                                  state=self.crew.memory.state(bot.name, task.id)),
            resume_session_id=task.session_id,
            # Work it started on its own reaches nothing outside crewbots, so it gets none of the bot's own MCP servers:
            # which of their tools only read can't be told from their names.
            mcp_servers={**({} if proactive else bot.mcp),
                         **({} if in_process else self._crew_tools(token, bot))},
            proactive=proactive,
            crew=CrewTools(self, task.id, self.is_coordinator(bot.name)) if in_process else None,
            deny_read=self._state_denials(bot.name),
            read_dirs=[*self.crew.skill_dirs, *[d for d in (self.crew.memory.root, self.crew.home / attachments_module.FOLDER,
                                                            self.crew.pages_of(bot))
                                                 if d.is_dir()]], timeout_s=bot.timeout_s,
            write_dirs=[Path(g["folder"]) for g in grants if g["kind"] == "folder"],
            network=True if any(g["kind"] == "network" for g in grants) else bot.network,
            effort=bot.effort, context_window=bot.context_window,
        )

    def _state_denials(self, bot: str) -> list[Path]:
        root = self.crew.memory.root
        names = set(self.crew.bots) | {p.name for p in root.glob("*") if p.is_dir() and not p.name.startswith(".")}
        return [root / ".git", *[root / name / "working-state" for name in sorted(names) if name != bot]]

    def _stay_awake(self) -> None:
        """Holds a power assertion while any task is running, and only then: work in progress shouldn't stall
        because the Mac went to sleep, but an idle crew shouldn't keep it awake."""
        if not self.keep_awake:
            return
        busy = any(t not in self._held for t in self._runs)
        if busy and (self._awake is None or self._awake.poll() is not None):
            # -w: let go if the daemon itself dies, so a crash never leaves the Mac unable to sleep.
            self._awake = subprocess.Popen([*self.keep_awake, "-w", str(os.getpid())],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif not busy and self._awake is not None:
            self._awake.terminate()
            self._awake = None

    def _crew_tools(self, token: str, bot: Bot, tools: str = "crew", flags: str = "") -> dict[str, McpServer]:
        if not self.api_url:
            return {}
        command, *args = self.mcp_command
        env = {"CREWBOTS_URL": self.api_url, "CREWBOTS_TOKEN": token,
               "CREWBOTS_DELEGATE": "1" if self.is_coordinator(bot.name) else "0",
               "CREWBOTS_CHIEF": "1" if self.is_chief(bot.name) else "0",
               "CREWBOTS_TOOLS": tools, "CREWBOTS_REVIEW": flags,
               "CREWBOTS_HOME": str(self.crew.home)}  # for the crew's own tool descriptions (prompts/tools/)
        return {MCP_SERVER_NAME: McpServer(command=command, args=list(args), env=env)}

    def _message(self, task_id: str, sender: str, text: str, attachments: list[dict[str, Any]] | None = None) -> None:
        self._record(task_id, kind="message", data={"from": sender, "text": text,
                                                    **({"attachments": attachments} if attachments else {})})

    def _record(self, task_id: str, event: Event | None = None, *, kind: str | None = None,
                data: dict[str, Any] | None = None) -> None:
        stored = self.store.append_event(task_id, event, kind=kind, data=data)
        self.bus.publish({"type": "event", "event": stored.to_json()})

    def _set(self, task_id: str, **fields: Any) -> Task:
        task = self.store.update_task(task_id, **fields)
        if task.bot in self.crew.bots and fields.get("status") in (Status.IDLE, Status.FAILED, Status.CANCELLED):
            if fields["status"] in (Status.FAILED, Status.CANCELLED):
                self._close_questions(task.id)
            self._refresh_working_state(task.bot)
        self.bus.publish({"type": "task", "task": {**task.to_json(), "attempt": self._retrying(task_id)}})
        if self.restarting and "status" in fields:  # how many turns the restart still waits for
            self.bus.publish({"type": "crew", "crew": self.state()})
        return task
