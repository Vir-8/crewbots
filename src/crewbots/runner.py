"""Runs tasks: each one on its owner bot's backend, in its own worktree when it writes, with every event stored and
published as it happens.

A follow-up message to a task resumes the same backend session. Messages that arrive while the bot is mid-run wait
and go in together as the next turn, so a task never has two runs at once.

Each active task keeps its CLI running between turns (a long-lived session), so a follow-up, a review round or the
chief waking up doesn't pay for start-up. A session is closed after it has been idle a while, when its task is
cancelled, or when the daemon stops, and a backend without sessions, or a session that won't open, falls back to
one process per turn, resuming from the stored session id.

Delegation: a coordinator bot creates child tasks through the crewbots MCP tools, then ends its turn. When none of its
children are still running, their handoffs and results go back to it as one message, which starts its next turn. It
never waits or polls. Each run gets a token that lets its MCP server act for that task only, and only while it runs.

Chats and rooms: each bot has one ongoing chat with the user, which never ends; real work goes into threads opened
from it. A room is a group chat. A message there wakes the bots it @mentions, or the room's lead if it mentions no
one; a bot's reply is posted back to the room and wakes whoever it mentions in turn, up to a few replies deep, so bots
can talk to each other without talking forever.

Budgets: before a turn starts, the runner checks the bot's budget, the budget of the routine run it belongs to, and
the plan's usage limit. If any is spent, the task is held: the turn waits (holding no run slot), and goes ahead by
itself once there's room again, or at once when the user releases it. A turn already running is never cut short.
"""

import asyncio
import contextlib
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import aclosing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crewbots import worktree
from crewbots.backends import BACKENDS, Access, Backend, McpServer, RunSpec
from crewbots.backends.base import Session
from crewbots.crew import CHAT_SETTING, NO_REPLY, SEAT_SETTING, Bot, Crew
from crewbots.crew_tools import CrewTools
from crewbots.events import Done, Event, RateLimit, SessionStarted, Usage
from crewbots.memory import CREW_SCOPE, Note
from crewbots.store import Handoff, Review, Status, Store, Task, Verdict

SUBSCRIBER_QUEUE = 10_000
MAX_CHILDREN = 12  # a stop condition: a coordinator that keeps delegating is looping, not managing
MAX_REVIEW_ROUNDS = 3  # a stop condition: after this many reviewed versions, the user decides
REPORT_RESULT_CHARS = 3000
IDLE_TIMEOUT_S = 600.0  # an idle session is closed after this; its next message resumes it from the stored id
MAX_SESSIONS = 8  # open sessions at once; past this the least recently used idle one is closed
ROLLOVER_PERCENT = 70.0  # past this much of its context window, a conversation continues in a fresh session
BRIEFING_PROMPT = (
    "This conversation is getting long, so it will continue in a fresh session that has none of it. Write the briefing "
    "that session will start from: the goal, what's been decided and why, what's done, the current state (files, "
    "branches, task ids), what's open, and what comes next. Be complete but brief. Reply with only the briefing, and "
    "don't use any tools."
)
BUDGET_REPORT_WINDOW = timedelta(days=7)  # what `budgets` reports for a bot without a budget
LIMIT_WINDOWS = {"five_hour": "five-hour", "seven_day": "weekly", "seven_day_opus": "weekly Opus",
                 "seven_day_sonnet": "weekly Sonnet", "5h": "five-hour", "7d": "weekly"}
MAX_ROOM_DEPTH = 4  # bot replies in a row in a room, after the user's message, before the bots stop waking each other
CREW_ROOM = "Crew"  # the group chat every bot is in, made when the crew first needs one
MENTION = re.compile(r"@([\w-]+)")
HOLD_RECHECK_S = 60.0  # a held task looks again this often: budgets free up as old usage ages out of their window
MCP_SERVER_NAME = "crewbots"
MCP_COMMAND = (sys.executable, "-m", "crewbots.mcp_server")


class UnknownBot(KeyError):
    pass


class NotAllowed(PermissionError):
    """A bot asked for something its role or the crew's rules don't allow. The message goes back to the bot."""


class MergeError(RuntimeError):
    """A merge that can't go ahead; nothing was changed."""


@dataclass(frozen=True)
class ReviewState:
    """Where a piece of work stands in review. A round is one reviewed version of the work (its `turns` when the
    reviews were asked for), so a review of an older version never approves newer changes."""

    work_id: str
    version: int  # the work as it is now
    rounds: list[int]  # the versions that were reviewed, in order
    latest: list[tuple[Task, Review | None]]  # the review tasks of the last round, with their verdicts

    @property
    def round(self) -> int:
        return len(self.rounds)

    @property
    def settled(self) -> bool:
        return bool(self.latest) and all(not t.active and r is not None for t, r in self.latest)

    @property
    def approved(self) -> bool:
        """Every review of the current version is in, and every one passed."""
        return (self.settled and self.rounds[-1] == self.version
                and all(r.verdict is Verdict.PASS for _, r in self.latest))

    @property
    def failed(self) -> bool:
        return self.settled and any(r.verdict is Verdict.FAIL for _, r in self.latest)

    @property
    def out_of_rounds(self) -> bool:
        return self.round >= MAX_REVIEW_ROUNDS and self.failed

    def to_json(self) -> dict[str, Any]:
        return {"work_id": self.work_id, "version": self.version, "round": self.round, "max_rounds": MAX_REVIEW_ROUNDS,
                "approved": self.approved, "failed": self.failed, "out_of_rounds": self.out_of_rounds,
                "reviews": [{"task_id": t.id, "reviewer": t.bot, "status": t.status,
                             "verdict": r.verdict if r else None, "summary": r.summary if r else None,
                             "findings": r.findings if r else []} for t, r in self.latest]}


@dataclass
class OpenSession:
    session: Session
    token: str  # the session's crew tools act with this for as long as the session is open
    cwd: Path
    last_used: float


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
        sessions: bool = True,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
        max_sessions: int = MAX_SESSIONS,
        keep_awake: tuple[str, ...] | None = None,  # a command that stops the Mac sleeping while it runs
        rollover_percent: float | None = ROLLOVER_PERCENT,
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
        self._tokens: dict[str, str] = {}  # run or session token → task id, while that run or session lasts
        self._escalated: set[str] = set()
        self.use_sessions = sessions
        self.idle_timeout_s = idle_timeout_s
        self.max_sessions = max_sessions
        self._sessions: dict[str, OpenSession] = {}
        self._reaper: asyncio.Task[None] | None = None
        self.keep_awake = keep_awake
        self.rollover_percent = rollover_percent
        self._awake: subprocess.Popen[bytes] | None = None
        self._resumed = asyncio.Event()  # cleared while the crew is paused: no new turn starts
        self._resumed.set()
        self._held: set[str] = set()  # tasks whose next turn waits on a budget or a plan limit
        self._released: set[str] = set()  # top-level tasks the user let past their budgets, until the daemon stops
        self._recheck = asyncio.Event()  # set when a hold may be over: a release, or a reloaded crew
        self.limits: dict[str, dict[str, RateLimit]] = {}  # plan → window → the latest the CLI reported
        self._seat_depth: dict[str, int] = {}  # seat task → the depth of the room message that woke it

    # Commands from the user (and, through `delegate` and `message_child`, from coordinators).

    def submit(self, bot: str, goal: str, *, created_by: str = "user", parent_id: str | None = None,
               workspace: str | None = None, review_of: str | None = None, review_version: int | None = None,
               routine: str | None = None, budget: int | None = None, title: str | None = None,
               pr: str | None = None) -> Task:
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        task = self.store.create_task(bot, goal, created_by=created_by, parent_id=parent_id, workspace=workspace,
                                      review_of=review_of, review_version=review_version, routine=routine,
                                      budget=budget, title=title, pr=pr)
        self.bus.publish({"type": "task", "task": task.to_json()})
        self._message(task.id, created_by, goal)
        self._start(task.id, goal)
        return task

    # Chats: a bot's one ongoing conversation with the user.

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

    def say(self, bot: str, text: str, sender: str = "user") -> Task:
        return self.send(self.chat(bot).id, text, sender=sender)

    def start_thread(self, task_id: str, title: str, goal: str) -> Task:
        """A bot opens a thread for itself, under the conversation it's in. Its result comes back there."""
        parent = self.store.get_task(task_id)
        if len(self.store.children(task_id)) >= self.max_children:
            raise NotAllowed(f"stop condition: this conversation already opened {self.max_children} threads. Finish or "
                             "report on those before opening more.")
        return self.submit(parent.bot, goal, created_by=parent.bot, parent_id=task_id, workspace=parent.workspace,
                           title=title)

    # Rooms: group chats.

    def crew_room(self) -> dict[str, Any]:
        """The group chat with every bot in it, led by the coordinator."""
        for room in self.store.rooms():
            if room["title"] == CREW_ROOM:
                return room
        bots = list(self.crew.bots.values())
        lead = next((b.name for b in bots if b.can_delegate), bots[0].name if bots else "")
        return self.create_room(CREW_ROOM, [b.name for b in bots], lead)

    def create_room(self, title: str, members: list[str], lead: str | None = None) -> dict[str, Any]:
        unknown = [m for m in members if m not in self.crew.bots]
        if unknown or not members:
            raise UnknownBot(f"no bots named {unknown}; the crew is {sorted(self.crew.bots)}" if unknown
                             else "a group chat needs at least one bot")
        lead = lead or members[0]
        if lead not in members:
            raise UnknownBot(f"the lead, {lead}, isn't in the group chat")
        room = self.store.create_room(title.strip() or ", ".join(members), lead, members)
        self.bus.publish({"type": "room", "room": room})
        return room

    def post(self, room_id: str, author: str, text: str, depth: int = 0) -> dict[str, Any]:
        """A message in a room, from the user or a bot. It wakes the bots it mentions; one from the user that mentions
        no one wakes the lead."""
        room = self.store.room(room_id)
        message = self.store.add_room_message(room_id, author, text, depth)
        self.bus.publish({"type": "room_message", "message": message})
        members = {m.lower(): m for m in room["members"]}
        for m in room["members"]:  # a bot can also be mentioned by the name you gave it, written as one word
            if (bot := self.crew.bots.get(m)) and bot.display_name:
                members.setdefault(bot.display_name.replace(" ", "").lower(), m)
        mentioned = list(dict.fromkeys(members[m.lower()] for m in MENTION.findall(text) if m.lower() in members))
        if author == "user":
            wake = mentioned or [room["lead"]]
        else:
            wake = [m for m in mentioned if m != author] if depth < MAX_ROOM_DEPTH else []
        for bot in wake:
            self._wake_seat(room, bot, depth)
        return message

    def post_from(self, task_id: str, text: str, room_id: str | None = None) -> dict[str, Any]:
        """The `post` tool: a bot says something in a room, from wherever it's working."""
        task = self.store.get_task(task_id)
        if room_id is None:
            room_id = task.room_id if task.kind == "seat" else self.crew_room()["id"]
        try:
            room = self.store.room(room_id)
        except KeyError:
            raise NotAllowed(f"no group chat {room_id}") from None
        if task.bot not in room["members"]:
            raise NotAllowed(f"{task.bot} isn't in the group chat {room['title']!r}")
        return self.post(room_id, task.bot, text, depth=self._seat_depth.get(task_id, 0) + 1)

    def _wake_seat(self, room: dict[str, Any], bot: str, depth: int) -> None:
        if bot not in self.crew.bots:
            return  # a member since removed from the crew
        seat = self.store.seat_of(room["id"], bot) or self.store.create_task(
            bot, f"{room['title']} ({bot})", kind="seat", room_id=room["id"], status=Status.IDLE)
        key = f"seat:{seat.id}"
        unseen = [m for m in self.store.room_messages(room["id"], after=self.store.seen(key)) if m["author"] != bot]
        if not unseen:
            return
        self.store.mark_seen(key, unseen[-1]["id"])
        # Woken again mid-turn, it keeps the deeper of the two, so a back-and-forth can't reset its own count.
        running = seat.id in self._runs
        self._seat_depth[seat.id] = max(depth, self._seat_depth.get(seat.id, 0)) if running else depth
        self.send(seat.id, "\n\n".join(f"[{m['author']}]: {m['text']}" for m in unseen), sender="room")

    def _seat_replied(self, seat: Task, text: str) -> None:
        text = text.strip()
        if not text or text.strip("`*_. ") == NO_REPLY:
            return
        self.post(seat.room_id, seat.bot, text, depth=self._seat_depth.get(seat.id, 0) + 1)

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
        opened = self._sessions.get(task_id)
        if not (opened and opened.session.alive and getattr(opened.session, "interruptible", False)):
            await self._close_session(task_id)  # an interruptible session stopped its turn and stays warm
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
        if self._reaper:
            self._reaper.cancel()
        self._resumed.set()
        for task_id in list(self._sessions):
            await self._close_session(task_id)
        for task_id in runs:
            self._set(task_id, status=Status.INTERRUPTED, error="the daemon stopped during this task")

    def running(self) -> list[str]:
        return list(self._runs)

    def held(self) -> list[str]:
        return list(self._held)

    def queued(self, task_id: str) -> list[str]:
        """Messages waiting for the task's current turn to finish."""
        return list(self._waiting.get(task_id, []))

    # Pausing: no new turn starts while paused; turns already running finish. Waiting tasks stay queued.

    @property
    def paused(self) -> bool:
        return not self._resumed.is_set()

    def pause(self) -> None:
        self._resumed.clear()
        self.bus.publish({"type": "crew", "crew": self.state()})

    def resume(self) -> None:
        self._resumed.set()
        self.bus.publish({"type": "crew", "crew": self.state()})

    def state(self) -> dict[str, Any]:
        limits = [asdict(limit) for windows in self.limits.values() for limit in windows.values()]
        return {"paused": self.paused, "running": len(self._runs) - len(self._held), "held": len(self._held),
                "sessions": len(self._sessions), "limits": [{k: v for k, v in x.items() if k != "kind"} for x in limits]}

    def reload(self, crew: Crew) -> None:
        """Takes up edited role cards (and their budgets). Running turns carry on as they started."""
        self.crew = crew
        self._wake_held()
        self.bus.publish({"type": "crew", "crew": self.state()})

    # Budgets.

    def release(self, task_id: str) -> Task:
        """The user lets a held task, and the rest of its goal, go past its budgets."""
        task = self.store.get_task(task_id)
        root = self.store.work_of(task_id)
        self._released.add(root.id)
        self._record(task_id, kind="released", data={"goal": root.id})
        self._wake_held()
        return task

    def budgets(self) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        rows = []
        for bot in self.crew.bots.values():
            window = bot.budget.window if bot.budget else BUDGET_REPORT_WINDOW
            rows.append({"bot": bot.name, "tokens": bot.budget.tokens if bot.budget else None,
                         "per": bot.budget.per if bot.budget else "week",
                         "used": self.store.bot_usage(bot.name, _iso(now - window)),
                         "held": sorted(t for t in self._held if self.store.get_task(t).bot == bot.name)})
        return rows

    def hold_reason(self, task: Task) -> str | None:
        """Why the task's next turn has to wait, or None if it can go ahead."""
        root = self.store.work_of(task.id)
        bot = self.crew.bots.get(task.bot)
        if root.id in self._released or bot is None:
            return None
        if bot.budget:
            used = self.store.bot_usage(bot.name, _iso(datetime.now(UTC) - bot.budget.window))
            if used >= bot.budget.tokens:
                return (f"{bot.name} has used {used:,} of its {bot.budget.tokens:,} tokens for the last {bot.budget.per}. "
                        "It goes on by itself as older use leaves that window, or when you release it.")
        if root.budget:
            used = self.store.tree_usage(root.id)
            if used >= root.budget:
                return (f"this run of the routine {root.routine!r} has used {used:,} of its {root.budget:,} tokens. "
                        "Release it to let the run finish.")
        plan = _plan(bot.backend)
        for limit in self.limits.get(plan, {}).values():
            if limit.status == "limited" and limit.resets_at and limit.resets_at > time.time():
                resets = datetime.fromtimestamp(limit.resets_at).strftime("%a %H:%M")
                window = LIMIT_WINDOWS.get(limit.window, limit.window.replace("_", " "))
                return f"the {plan.title()} plan's {window} limit is reached; it resets {resets}."
        return None

    async def _within_budget(self, task_id: str) -> None:
        held = False
        try:
            while reason := self.hold_reason(self.store.get_task(task_id)):
                if not held:
                    held = True
                    self._held.add(task_id)
                    self._stay_awake()  # a held task isn't work in progress
                    self._set(task_id, status=Status.HELD)
                    self._record(task_id, kind="held", data={"reason": reason})
                    self.bus.publish({"type": "crew", "crew": self.state()})
                recheck = self._recheck
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(recheck.wait(), HOLD_RECHECK_S)
                await self._resumed.wait()
        finally:
            if held:
                self._held.discard(task_id)
                self._stay_awake()
                self.bus.publish({"type": "crew", "crew": self.state()})

    def _wake_held(self) -> None:
        self._recheck.set()
        self._recheck = asyncio.Event()

    def _note_limit(self, task_id: str, limit: RateLimit) -> None:
        """Keeps the plan's latest limit, and tells the crew when its status changes (getting close, reached)."""
        windows = self.limits.setdefault(limit.plan, {})
        previous = windows.get(limit.window)
        windows[limit.window] = limit
        if (previous.status if previous else "ok") != limit.status:
            self._record(task_id, kind="rate_limit", data={k: v for k, v in asdict(limit).items() if k != "kind"})
            self.bus.publish({"type": "crew", "crew": self.state()})

    def open_sessions(self) -> list[str]:
        return list(self._sessions)

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
        version = None
        if review_of:
            work = self._own_child(parent_id, review_of)
            if work.active or work.id in self._runs:
                raise NotAllowed(f"{work.id} is still working; ask for a review once it has finished")
            if work.review_of:
                raise NotAllowed(f"{work.id} is itself a review; review the work it reviewed instead")
            state = self.review_state(work.id)
            version = work.turns
            if version not in state.rounds and state.round >= MAX_REVIEW_ROUNDS:
                raise NotAllowed(
                    f"stop condition: {work.id} has had {MAX_REVIEW_ROUNDS} review rounds. Report to the user with the "
                    "best version and the open objections, instead of another round (team/rules.md).")
            workspace = work.worktree_path or work.workspace
        return self.submit(bot, goal, created_by=parent.bot, parent_id=parent_id,
                           workspace=workspace or parent.workspace, review_of=review_of, review_version=version)

    def children_of(self, parent_id: str) -> list[dict[str, Any]]:
        return [self._summary(c, include_result=True) for c in self.store.children(parent_id)]

    def message_child(self, parent_id: str, task_id: str, text: str) -> Task:
        child = self._own_child(parent_id, task_id)
        if child.status is not Status.NEEDS_INPUT and self.review_state(task_id).out_of_rounds:
            raise NotAllowed(
                f"stop condition: {task_id} failed review in all {MAX_REVIEW_ROUNDS} rounds. Report to the user with "
                "the best version and the open objections, instead of sending it back again (team/rules.md).")
        return self.send(task_id, text, sender=self.store.get_task(parent_id).bot)

    def remember(self, task_id: str, title: str, body: str, shared: bool = False) -> Note:
        task = self.store.get_task(task_id)
        scope = CREW_SCOPE if shared else task.bot
        note = self.crew.memory.remember(scope, title, body, source=f"{task.id} ({task.bot})")
        self._record(task_id, kind="memory", data={"action": "remember", "scope": scope, "title": note.title,
                                                   "body": note.body, "path": str(note.path)})
        return note

    def forget(self, task_id: str, name: str, shared: bool = False) -> Note:
        task = self.store.get_task(task_id)
        scope = CREW_SCOPE if shared else task.bot
        note = self.crew.memory.forget(scope, name)
        self._record(task_id, kind="memory", data={"action": "forget", "scope": scope, "title": note.title})
        return note

    def recall(self, task_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """The best matches in the crew's record, one per task, leaving out the asking task's own conversation."""
        hits, seen = [], set()
        for hit in self.store.search(query, limit=limit * 4, exclude_task=task_id):
            if hit["task_id"] not in seen:
                seen.add(hit["task_id"])
                hits.append(hit)
            if len(hits) == limit:
                break
        return hits

    def review(self, task_id: str, verdict: Verdict, summary: str, findings: list[dict[str, str]]) -> Review:
        task = self.store.get_task(task_id)
        if not task.review_of:
            raise NotAllowed("only a review task gives a verdict; this task is not a review")
        review = self.store.add_review(task_id, task.review_of, task.bot, task.review_version or 0, verdict, summary,
                                       findings)
        self._record(task_id, kind="review", data=review.to_json())
        self._record(task.review_of, kind="review", data=review.to_json())
        return review

    def review_state(self, work_id: str) -> ReviewState:
        work = self.store.get_task(work_id)
        reviews = self.store.reviewers_of(work_id)
        rounds = sorted({r.review_version for r in reviews if r.review_version is not None})
        latest = [(r, self.store.latest_review(r.id)) for r in reviews if rounds and r.review_version == rounds[-1]]
        return ReviewState(work_id=work_id, version=work.turns, rounds=rounds, latest=latest)

    def propose_merge(self, parent_id: str, task_id: str, summary: str) -> Task:
        """A coordinator puts approved work in front of the user. Only the user merges."""
        work = self._own_child(parent_id, task_id)
        if not work.worktree_path:
            raise NotAllowed(f"{task_id} has no worktree, so there is nothing to merge")
        if work.merge_state == "merged":
            raise NotAllowed(f"{task_id} is already merged")
        state = self.review_state(task_id)
        if not state.approved:
            raise NotAllowed(
                f"{task_id} is not approved: every review of its current version must pass first "
                f"(round {state.round} of {MAX_REVIEW_ROUNDS}, version {state.version}, "
                f"reviewed versions {state.rounds or 'none'})")
        task = self._set(task_id, merge_state="proposed")
        self._record(task_id, kind="merge_proposed", data={"by": self.store.get_task(parent_id).bot, "summary": summary})
        return task

    def merge(self, task_id: str, force: bool = False) -> Task:
        """The user's merge: commits the worktree, merges its branch into the repo's current branch, removes the
        worktree. Refuses work that is still running or not approved (unless forced); a conflict changes nothing."""
        work = self.store.get_task(task_id)
        if not work.worktree_path:
            raise MergeError(f"{task_id} has no worktree")
        if work.merge_state == "merged":
            raise MergeError(f"{task_id} is already merged ({work.merged_commit})")
        if work.active or task_id in self._runs:
            raise MergeError(f"{task_id} is still working")
        state = self.review_state(task_id)
        if not state.approved and not force:
            raise MergeError(
                f"{task_id} is not approved (round {state.round}, reviewed versions {state.rounds or 'none'}, "
                f"current version {state.version}); pass force to merge anyway")
        wt = worktree.Worktree(repo=Path(work.repo), path=Path(work.worktree_path), branch=work.branch, base=work.base)
        reviewers = ", ".join(f"{t.bot} ({r.verdict if r else 'no verdict'})" for t, r in state.latest) or "none"
        try:
            worktree.commit(wt, f"{work.title}\n\ncrewbots task {work.id}, by {work.bot}")
            commit = worktree.merge(wt, f"Merge {work.branch}: {work.title}\n\nReviewed in round {state.round}: "
                                        f"{reviewers}.{' Merged without approval.' if not state.approved else ''}")
        except worktree.GitError as e:
            raise MergeError(str(e)) from None
        worktree.remove(wt, delete_branch=True)
        task = self._set(task_id, merge_state="merged", merged_commit=commit)
        self._record(task_id, kind="merged", data={"commit": commit, "into": str(wt.repo), "forced": not state.approved})
        return task

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
        self._stay_awake()
        if self.use_sessions and (self._reaper is None or self._reaper.done()):
            self._reaper = asyncio.create_task(self._reap_idle(), name="session reaper")

    async def _turns(self, task_id: str, prompt: str | None) -> None:
        try:
            while prompt is not None:
                await self._run(task_id, prompt)
                waiting = self._waiting.pop(task_id, None)
                prompt = "\n\n".join(waiting) if waiting else None
        finally:
            self._runs.pop(task_id, None)
            self._stay_awake()
        self._report_up(task_id)

    async def _run(self, task_id: str, prompt: str) -> None:
        await self._resumed.wait()
        await self._within_budget(task_id)
        async with self._slots:
            task = self._set(task_id, status=Status.RUNNING, error=None)
            try:
                bot = self.crew.bots[task.bot]
                cwd = self._cwd(task, bot)
            except Exception as e:  # a bot removed from the crew, a missing workspace, a git failure
                self._record(task_id, Done(final_text="", is_error=True, error=f"crewbots: {e}"))
                self._set(task_id, status=Status.FAILED, error=str(e))
                return
            backend = self.backends[bot.backend]
            open_session = await self._session_for(task, bot, cwd, backend)
            token = open_session.token if open_session else secrets.token_urlsafe(32)
            self._tokens[token] = task_id
            done: Done | None = None
            try:
                if open_session:
                    stream = open_session.session.turn(prompt)
                else:
                    stream = backend.run(self._spec(task, bot, cwd, token, prompt, backend))
                async with aclosing(stream) as stream:
                    async for event in stream:
                        if isinstance(event, RateLimit):
                            self._note_limit(task_id, event)
                            continue
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
                if open_session is None:
                    self._tokens.pop(token, None)
                elif open_session.session.alive:
                    open_session.last_used = asyncio.get_running_loop().time()
                else:
                    await self._close_session(task_id)  # it died, or was stopped mid-turn: the next turn resumes
            if done is None or done.is_error:
                self._escalated.discard(task_id)
                self._set(task_id, status=Status.FAILED, result=done.final_text if done else None,
                          error=done.error if done else "the run ended without a result")
            elif task_id in self._escalated:
                self._escalated.discard(task_id)
                self._set(task_id, status=Status.NEEDS_INPUT, result=done.final_text, turns=task.turns + 1)
            else:
                if task.kind == "seat" and task.room_id:
                    self._seat_replied(task, done.final_text)
                if open_session and open_session.session.alive:
                    await self._maybe_roll_over(task_id, open_session)
                # Not idle yet if messages are waiting: the next turn starts right away, and watchers should not stop.
                next_status = Status.QUEUED if self._waiting.get(task_id) else Status.IDLE
                self._set(task_id, status=next_status, result=done.final_text, turns=task.turns + 1)

    def _spec(self, task: Task, bot: Bot, cwd: Path, token: str, prompt: str, backend: Backend) -> RunSpec:
        # A backend that hosts tools in-process gets the crew tools as Python; any other, as an MCP server with a token.
        in_process = getattr(backend, "in_process_tools", False) and self.api_url is not None
        return RunSpec(
            prompt=prompt, cwd=cwd, access=bot.access, model=bot.model,
            system_prompt=self.crew.system_prompt(bot, delegated_by=task.created_by if task.parent_id else None,
                                                  reviewing=self._review_brief(task), briefing=task.briefing,
                                                  routine=task.routine, setting=self._setting(task)),
            resume_session_id=task.session_id,
            mcp_servers={**bot.mcp, **({} if in_process else self._crew_tools(token, bot))},
            crew=CrewTools(self, task.id, bot.can_delegate) if in_process else None,
            read_dirs=[d for d in (self.crew.skills_dir, self.crew.memory.root) if d.is_dir()], timeout_s=bot.timeout_s,
            network=bot.network,
        )

    # Sessions.

    async def _session_for(self, task: Task, bot: Bot, cwd: Path, backend: Backend) -> OpenSession | None:
        """The task's open session, or a new one; None to run this turn as its own process."""
        current = self._sessions.get(task.id)
        if current and current.session.alive and current.cwd == cwd:
            return current
        if current:
            await self._close_session(task.id)
        if not self.use_sessions or not hasattr(backend, "open"):
            return None
        await self._make_room()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = task.id  # its MCP server may call in while the session starts
        try:
            session = await backend.open(self._spec(task, bot, cwd, token, "", backend))
        except Exception as e:
            self._tokens.pop(token, None)
            self._record(task.id, kind="notice", data={"message": f"couldn't keep a session open ({e}); "
                                                                  "running this turn on its own"})
            return None
        opened = OpenSession(session=session, token=token, cwd=cwd, last_used=asyncio.get_running_loop().time())
        self._sessions[task.id] = opened
        return opened

    async def _maybe_roll_over(self, task_id: str, opened: OpenSession) -> None:
        """A conversation past `rollover_percent` of its context gets a briefing, written by its own bot, and the next
        turn starts a fresh session from it, instead of the CLI compacting it over and over."""
        measure = getattr(opened.session, "context_percent", None)
        if not self.rollover_percent or measure is None:
            return
        percent = await measure()
        if percent is None or percent < self.rollover_percent:
            return
        briefing, usage = "", None
        try:
            async with aclosing(opened.session.turn(BRIEFING_PROMPT)) as stream:
                async for event in stream:
                    if isinstance(event, Usage):
                        usage = event
                    elif isinstance(event, Done) and not event.is_error:
                        briefing = event.final_text
        except Exception:
            briefing = ""
        if usage:  # it counts toward budgets like any turn
            self.store.append_event(task_id, usage)
            self.store.add_usage(task_id, usage.input_tokens, usage.output_tokens, usage.cost_usd)
        if not briefing.strip():
            return  # no briefing, no rollover: the CLI's own compaction carries on
        await self._close_session(task_id)
        self.store.update_task(task_id, briefing=briefing, session_id=None)
        self._record(task_id, kind="rollover", data={"percent": round(percent, 1), "briefing": briefing})

    async def _close_session(self, task_id: str) -> None:
        opened = self._sessions.pop(task_id, None)
        if opened:
            self._tokens.pop(opened.token, None)
            with contextlib.suppress(Exception):
                await opened.session.close()

    async def _make_room(self) -> None:
        idle = sorted((s.last_used, t) for t, s in self._sessions.items() if t not in self._runs)
        while len(self._sessions) >= self.max_sessions and idle:
            await self._close_session(idle.pop(0)[1])

    async def _reap_idle(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(max(1.0, min(60.0, self.idle_timeout_s / 4)))
            now = loop.time()
            for task_id, opened in list(self._sessions.items()):
                if task_id not in self._runs and now - opened.last_used >= self.idle_timeout_s:
                    await self._close_session(task_id)

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

    def _setting(self, task: Task) -> str | None:
        if task.kind == "chat":
            return CHAT_SETTING
        if task.kind == "seat" and task.room_id:
            room = self.store.room(task.room_id)
            others = [m for m in room["members"] if m != task.bot]
            lead = ("You lead it: a message that mentions no one is yours to answer." if room["lead"] == task.bot
                    else f"{room['lead']} leads it; you're woken when someone mentions you.")
            return SEAT_SETTING.format(title=room["title"], others=", ".join(others) or "no other bots", lead=lead)
        return None

    def _review_brief(self, task: Task) -> str | None:
        if not task.review_of:
            return None
        work = self.store.get_task(task.review_of)
        rounds = self.review_state(work.id).rounds
        n = rounds.index(task.review_version) + 1 if task.review_version in rounds else len(rounds)
        return (f"This task is review round {n} of {MAX_REVIEW_ROUNDS} of task {work.id} ({work.bot}: {work.title}). "
                "Its work is in your working directory: the uncommitted changes (`git diff`, and new files) are what "
                "you review. Finish by calling `review(verdict, summary, findings)`: verdict `pass` or `fail`, one "
                "finding per problem, each with its location, the problem, and a failure scenario. A pass with "
                "remaining risks is still a pass; list them as findings.")

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
        self._set(task.id, repo=str(wt.repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base)  # watchers see it now
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
        sections = "\n\n".join(
            _report_section(s, self.store.latest_handoff(s.id), self.store.latest_review(s.id)) for s in finished)
        reviewed = dict.fromkeys(s.review_of for s in finished if s.review_of)
        status = "\n".join(_round_status(self.review_state(w), self.store.get_task(w)) for w in reviewed)
        self.send(task.parent_id, sender="crewbots", text=(
            f"{len(finished)} thread(s) you opened or delegated finished, and none are still running.\n\n{sections}"
            + (f"\n\n# Review status\n\n{status}" if status else "")))

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


def _report_section(task: Task, handoff: Handoff | None, review: Review | None = None) -> str:
    lines = [f"## {task.id} · {task.bot} · {task.status}", f"Task: {task.title}"]
    if task.review_of:
        lines.append(f"Review of {task.review_of} (version {task.review_version}): "
                     + (review.verdict.upper() if review else "NO VERDICT RECORDED"))
        if review:
            lines.append(f"Summary: {review.summary}")
            lines += [f"- {f.get('location', '?')}: {f.get('problem', '')}"
                      + (f" Scenario: {f['scenario']}" if f.get("scenario") else "") for f in review.findings]
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


def _round_status(state: ReviewState, work: Task) -> str:
    head = f"{work.id} ({work.bot}): round {state.round} of {MAX_REVIEW_ROUNDS}"
    if not state.settled:
        return f"{head}: some reviews of this round gave no verdict; ask them with message, or review again."
    if state.approved:
        return (f"{head}: approved; every review passed. Propose it to the user with propose_merge(task_id="
                f"{work.id!r}, summary=...).")
    if state.out_of_rounds:
        return (f"{head}: STOP CONDITION. It failed review in every round. Do not send it back again: report to the "
                "user with the best version and the open objections.")
    if state.failed:
        return (f"{head}: changes requested. Send the findings you judge real to the owner with message(task_id="
                f"{work.id!r}, ...); its next version needs a new review round ({state.round + 1} of "
                f"{MAX_REVIEW_ROUNDS}).")
    return f"{head}: reviewed an older version ({state.rounds[-1]}); the work has changed since, so review it again."


def _plan(backend: str) -> str:
    """The subscription a backend runs on: both Claude backends share Claude's limits, both Codex ones Codex's."""
    return backend.split("-")[0]


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds")


def _clip(text: str | None) -> str | None:
    if text is None or len(text) <= REPORT_RESULT_CHARS:
        return text
    return text[:REPORT_RESULT_CHARS] + f"\n… ({len(text) - REPORT_RESULT_CHARS} more characters; ask the task for them)"
