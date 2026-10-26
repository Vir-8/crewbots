"""The daemon's API: HTTP for commands, one WebSocket for live updates. The desktop app and `crewbots` both use it.

It listens on localhost only, and every request needs a token. A browser tab can reach localhost too, so the Host
header is checked as well (a DNS-rebinding page arrives with its own host name).

Every route is under /v1. There are two kinds of token. The daemon's token, in daemon.json, is the user's and opens
everything but /v1/bot/. A run token is minted for one run of one task and opens only /v1/bot/, acting as that task:
that is what a bot's crewbots MCP server holds, so a bot can never act for another task, or for the user.
"""

import asyncio
import base64
import binascii
import contextlib
import json
import os
import secrets
import subprocess
import sys
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from crewbots import attachments as attachments_module
from crewbots import cards, mcp_catalog, typescript
from crewbots import crew as crew_module
from crewbots import models as models_module
from crewbots import plans as plans_module
from crewbots import routines as routines_module
from crewbots import service as service_module
from crewbots import usage as usage_module
from crewbots.backends import codex_isolation
from crewbots.crew_tools import ToolRefused
from crewbots.github import GitHubError
from crewbots.memory import MemoryError
from crewbots.reviews import INBOX_LISTS, SEARCHES, ReviewError, Reviews, ReviewTools, reviewer_name
from crewbots.routines import RoutineError
from crewbots.runner import MergeError, NotAllowed, Runner, UnknownBot
from crewbots.scheduler import Scheduler
from crewbots.store import Status, Store, Task, Verdict

DEFAULT_PORT = 8765
API_VERSION = 1  # the /v1 in every route; a change the app can't follow raises it
PROJECT = Path(__file__).resolve().parents[2]  # the checkout the daemon runs from, when it runs from one
RESTART_EXIT = 75  # EX_TEMPFAIL: stopped to be started again
CLIENT_ERRORS_PER_MINUTE = 30
LOG_CHECK_S = 600.0  # how often the daemon looks at its log's size
RESTART_TIMEOUT_S = 20 * 60.0  # how long a restart waits for running turns; then they're cut off and carried on
DAEMON_FILE = "daemon.json"  # in the crew's folder: where the daemon is and its token, readable only by the user


# What the API returns. The TypeScript types in app/src/api-types.ts are generated from these (`crewbots openapi`).


class TaskOut(BaseModel):
    id: str
    bot: str
    title: str
    status: Status
    created_by: str
    parent_id: str | None
    workspace: str | None
    repo: str | None
    worktree_path: str | None
    branch: str | None
    base: str | None
    session_id: str | None
    result: str | None
    error: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    reported: bool
    turns: int
    review_of: str | None
    review_version: int | None
    merge_state: Literal["proposed", "merged"] | None
    merged_commit: str | None
    kind: Literal["chat", "seat", "thread", "review"]
    room_id: str | None
    pr: str | None  # a code review's pull request, owner/name#number
    routine: str | None
    budget: int | None
    created_at: str
    updated_at: str


class EventOut(BaseModel):
    id: int
    task_id: str
    kind: str  # a backend event (text, tool_call, ...) or a crewbots one (message, handoff, review, ...)
    data: dict[str, Any]
    at: str


class BotOut(BaseModel):
    name: str
    title: str
    description: str
    backend: str
    model: str | None
    access: str
    workspace: str | None
    network: bool | list[str]  # its shell commands: none, any host, or these hosts
    mcp: list[str]  # the MCP servers its role card grants, besides crewbots' own
    skills: list[str]
    can_delegate: bool
    path: str | None  # its role card
    display_name: str | None  # what you call it; `name` is its handle
    effort: str | None  # reasoning effort; none: the CLI's default
    avatar: dict[str, str] | None  # {color, shape}; none: picked from its name
    context_window: int | None  # tokens its sessions hold before compacting; none: the model's whole window


class Avatar(BaseModel):
    color: str | None = None
    shape: str | None = None


class Budget(BaseModel):
    tokens: int
    per: Literal["day", "week"] = "week"


class CardBody(BaseModel):
    """A role card's editable parts. Fields left out are left as they are."""

    display_name: str | None = None
    title: str | None = None
    description: str | None = None
    backend: str | None = None
    model: str | None = None
    effort: str | None = None
    context_window: int | Literal["auto"] | None = None  # tokens, or the model's whole window; blank: the default
    access: Literal["read_only", "workspace_write", "full"] | None = None
    network: bool | list[str] | None = None
    avatar: Avatar | None = None
    budget: Budget | None = None
    mcp: list[str] | None = None  # the crew's MCP servers (mcp.json) it may use
    instructions: str | None = None  # the card's body: mission, what it owns, what done means


class NewBot(CardBody):
    name: str  # its handle


class CardOut(CardBody):
    name: str


class DraftOut(BaseModel):
    """A line comment for a review, private until the user submits it."""

    id: int
    path: str
    line: int
    side: Literal["RIGHT", "LEFT"]
    body: str
    author: str  # "user", or the reviewer that drafted it
    task_id: str | None
    commit_sha: str | None  # the head it was written against
    created_at: str
    updated_at: str


class DraftBody(BaseModel):
    path: str
    line: int
    side: Literal["RIGHT", "LEFT"] = "RIGHT"
    body: str


class DraftEdit(BaseModel):
    body: str


class SubmitReview(BaseModel):
    event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]
    body: str = ""
    drafts: list[int] | None = None  # which drafts go with it; None: all of them


class Submitted(BaseModel):
    url: str
    state: str  # GitHub's: APPROVED, CHANGES_REQUESTED or COMMENTED
    comments: int


class ImageOut(BaseModel):
    mime: str
    data: str  # base64


class McpServerOut(BaseModel):
    """One of the crew's MCP servers, without the values of its env or headers, which can be keys."""

    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None
    args: list[str]
    url: str | None
    env_keys: list[str]
    header_keys: list[str]
    bots: list[str]  # the bots whose cards name it


class McpServerBody(BaseModel):
    transport: Literal["stdio", "http", "sse"] = "stdio"
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] | None = None  # None: keep what it has
    url: str | None = None
    headers: dict[str, str] | None = None  # None: keep what it has


class InstalledServer(BaseModel):
    """A server one of your other apps has, that the crew could copy."""

    id: str
    source: str  # "Claude Code", "Claude Code · <project>", "Claude desktop" or "Codex"
    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None
    args: list[str]
    url: str | None
    env_keys: list[str]
    header_keys: list[str]
    in_crew: bool  # the crew has a server by that name already


class ImportBody(BaseModel):
    id: str
    name: str | None = None  # to add it under another name


class RegistryField(BaseModel):
    kind: Literal["env", "arg", "header"]
    key: str
    description: str
    required: bool
    secret: bool
    default: str | None


class RegistryOption(BaseModel):
    id: str  # package:<n> or remote:<n>
    label: str
    fields: list[RegistryField]


class RegistryEntry(BaseModel):
    name: str
    title: str | None
    description: str
    version: str
    website: str | None
    options: list[RegistryOption]


class RegistryAdd(BaseModel):
    server: str  # its name in the registry
    version: str
    option: str
    name: str  # what the crew calls it
    values: dict[str, str] = {}


class SkillOut(BaseModel):
    name: str
    description: str
    source: str  # "crew", or the global folder it's in, such as ~/.claude/skills
    path: str
    used: bool  # False: a skill of the same name was found first, and that one is used
    same_as: str | None  # for one not used, where the one used is


class McpCheck(BaseModel):
    ok: bool
    tools: list[str]
    error: str | None


class PullSummary(BaseModel):
    repo: str
    number: int
    title: str
    url: str
    draft: bool
    author: str
    author_avatar: str | None
    created_at: str | None
    updated_at: str | None
    additions: int
    deletions: int
    changed_files: int
    review_decision: str | None
    checks: str | None  # SUCCESS, FAILURE, PENDING, ... for the head commit
    reviewed_at: str | None = None  # when a review of yours in crewbots was last touched


class Inbox(BaseModel):
    """Pull requests waiting on you, which never include your own, and your own, apart. Only the lists asked for are
    there."""

    assigned: list[PullSummary] | None = None  # assigned to you
    direct: list[PullSummary] | None = None  # asking you for a review
    team: list[PullSummary] | None = None  # asking one of your teams
    mine: list[PullSummary] | None = None  # yours
    error: str | None  # GitHub's, for a list it couldn't read; that list is the last good one, or empty


class PullFile(BaseModel):
    path: str
    additions: int
    deletions: int


class Checks(BaseModel):
    total: int
    passed: int
    failed: int


class PullOut(BaseModel):
    repo: str
    number: int
    title: str
    body: str
    url: str
    state: str | None
    draft: bool
    author: str
    head: str
    base: str
    head_sha: str
    base_sha: str = ""
    additions: int
    deletions: int
    changed_files: int
    review_decision: str | None
    created_at: str | None
    updated_at: str | None
    labels: list[str]
    files: list[PullFile]
    checks: Checks
    failing: list[str]  # the checks that fail
    issues: list[int]  # the issues it refers to
    people: list[str] = []  # who a comment might @mention: the author first
    checkout: str | None  # the local copy reviews read, if the repo is mapped
    reviews: list[TaskOut]


class ReviewRequest(BaseModel):
    note: str = ""  # anything to look at in particular


class Reviewer(BaseModel):
    backend: Literal["claude", "codex"]
    model: str | None = None  # None: the CLI's own default
    effort: str | None = None


class ModelOut(BaseModel):
    id: str
    label: str
    description: str
    efforts: list[str]
    default_effort: str | None
    default: bool


class ModelsOut(BaseModel):
    claude: list[ModelOut]
    codex: list[ModelOut]


class RepoFolder(BaseModel):
    folder: str | None


class TaskStatus(BaseModel):
    """What /status shows for a chat or a thread."""

    task_id: str
    bot: str
    backend: str | None
    plan: str | None  # claude or codex: whose limits it uses
    model: str | None  # None: the CLI's own default
    effort: str | None
    session_id: str | None
    open: bool  # its session is open now; a closed one resumes from session_id
    context_percent: float | None  # how full its context is, when last measured
    context_window: int | None  # the tokens it's measured against; None: the model's whole window
    rollover_percent: float | None  # past this, it continues in a fresh session from a briefing
    turns: int


class PlanWindow(BaseModel):
    name: str  # in the CLI's own terms: "Session", "Week · Fable", "5 hours", "Week"
    used_percent: float
    resets_at: int | None  # unix time


class PlanOut(BaseModel):
    available: bool
    error: str | None
    windows: list[PlanWindow]
    notes: list[str]  # anything else the CLI says about the plan: credits left, free resets, why there are no limits
    checked_at: float  # unix time; older than a couple of minutes means a fresh answer is on its way


class PlansOut(BaseModel):
    claude: PlanOut
    codex: PlanOut


class UsageDay(BaseModel):
    date: str
    claude: int
    codex: int


class UsagePeak(BaseModel):
    date: str
    tokens: int


class LongestTurn(BaseModel):
    seconds: int
    task_id: str | None
    title: str


class Streak(BaseModel):
    longest: int
    current: int


class ModelUse(BaseModel):
    plan: str
    model: str
    tokens: int
    input: int  # all input, cached or not
    cached: int  # input the model had cached


class BotUse(BaseModel):
    bot: str
    tokens: int
    input: int
    cached: int


class CacheUse(BaseModel):
    input: int
    cached: int


class EffortUse(BaseModel):
    effort: str
    turns: int


class UsageCounts(BaseModel):
    threads: int
    reviews: int
    routine_runs: int
    room_messages: int


class UsageSummary(BaseModel):
    """The crew's own use, in fresh tokens."""

    days: int
    daily: list[UsageDay]
    totals: dict[str, int]
    turns: int
    peak: UsagePeak | None
    longest_turn: LongestTurn | None
    streak: Streak
    models: list[ModelUse]
    bots: list[BotUse]
    efforts: list[EffortUse]
    counts: UsageCounts
    cache: CacheUse


class RoutineBody(BaseModel):
    description: str | None = None
    bot: str | None = None
    schedule: str | None = None
    on: str | None = None
    repo: str | None = None
    workspace: str | None = None
    budget: int | None = None
    enabled: bool | None = None
    goal: str | None = None


class NewRoutine(RoutineBody):
    name: str


class RoutineFileOut(RoutineBody):
    name: str


class FindingOut(BaseModel):
    location: str | None = None
    problem: str | None = None
    scenario: str | None = None


class ReviewEntryOut(BaseModel):
    task_id: str
    reviewer: str
    status: Status
    verdict: Verdict | None
    summary: str | None
    findings: list[FindingOut]


class ReviewStateOut(BaseModel):
    work_id: str
    version: int
    round: int
    max_rounds: int
    approved: bool
    failed: bool
    out_of_rounds: bool
    reviews: list[ReviewEntryOut]


class HandoffOut(BaseModel):
    id: int
    task_id: str
    from_bot: str
    done: str
    output: list[str]
    open: str
    needs: str
    at: str


class ChildOut(BaseModel):
    id: str
    bot: str
    title: str
    status: Status
    worktree: str | None = None
    branch: str | None = None
    handoff: HandoffOut | None = None
    result: str | None = None
    error: str | None = None


class CursorOut(BaseModel):
    id: int


class ChangesOut(BaseModel):
    diff: str | None


class PlanLimitOut(BaseModel):
    plan: str
    window: str
    status: Literal["ok", "warning", "limited"]
    used_percent: float | None
    resets_at: int | None


class VersionOut(BaseModel):
    """What the daemon is, so the app can tell when one of them is out of date."""

    api: int  # the major version: the app and the daemon must agree on it
    schema_hash: str  # a fingerprint of every route and model; the app is built with its own
    build: str | None  # the commit the daemon started from, when it runs from a git checkout
    source_changed: bool  # the checkout has moved on since: a restart would update the daemon


class ClientError(BaseModel):
    """Something in the app's window that failed, for the daemon's log: the app has no terminal of its own."""

    where: str
    message: str
    stack: str | None = None


class RestartBody(BaseModel):
    when_idle: bool = True
    timeout_s: float = RESTART_TIMEOUT_S


class CrewState(BaseModel):
    paused: bool
    restarting: bool  # the daemon restarts once no turn is running; meanwhile none starts
    running: int
    turns_running: int  # under way now, not waiting their turn or held
    held: int
    sessions: int
    limits: list[PlanLimitOut]


class RoutineOut(BaseModel):
    name: str
    description: str
    bot: str
    when: str  # the cron schedule, or the trigger
    schedule: str | None
    on: str | None
    repo: str | None
    workspace: str | None
    budget: int | None
    enabled: bool
    path: str
    last_run_at: str | None
    last_task_id: str | None
    next_run_at: str | None
    skipped_at: str | None


class StarterOut(BaseModel):
    name: str
    description: str
    when: str


class RoutinesOut(BaseModel):
    routines: list[RoutineOut]
    errors: dict[str, str]  # routine file → why it couldn't be read
    github_error: str | None
    crew_error: str | None  # a role card or team file that couldn't be read; the crew carries on as it was
    starters: list[StarterOut]  # starter routines this crew doesn't have yet


class BudgetOut(BaseModel):
    bot: str
    tokens: int | None  # None: no budget
    per: Literal["day", "week"]
    used: int  # fresh tokens over the last day or week
    held: list[str]


class LiveCrew(BaseModel):
    type: Literal["crew"]
    crew: CrewState


class RoomOut(BaseModel):
    id: str
    title: str
    lead: str
    members: list[str]
    created_at: str
    updated_at: str


class AttachmentOut(BaseModel):
    id: str
    name: str
    path: str  # in the crew's folder, where bots read it
    mime: str
    size: int
    kind: Literal["image", "file"]


class RoomMessageOut(BaseModel):
    id: int
    room_id: str
    author: str  # user, or a bot's name
    text: str
    depth: int
    at: str
    attachments: list[AttachmentOut] = []


class LastMessage(BaseModel):
    author: str
    text: str
    at: str


class ChatOut(BaseModel):
    key: str  # bot:<name> or room:<id>
    kind: Literal["direct", "group"]
    title: str
    bot: str | None  # a direct chat's bot
    task_id: str | None  # a direct chat's task, whose events are the conversation
    room: RoomOut | None
    last: LastMessage | None
    unread: int
    working: bool
    updated_at: str


class OutputOut(BaseModel):
    path: str
    task_id: str
    title: str
    at: str


class ProfileOut(BaseModel):
    bot: str
    working: bool
    last_active: str | None
    threads: list[TaskOut]
    outputs: list[OutputOut]


class LiveRoom(BaseModel):
    type: Literal["room"]
    room: RoomOut


class LiveRoomMessage(BaseModel):
    type: Literal["room_message"]
    message: RoomMessageOut


class LiveTask(BaseModel):
    type: Literal["task"]
    task: TaskOut


class LiveEvent(BaseModel):
    type: Literal["event"]
    event: EventOut


LiveMessage = Annotated[LiveTask | LiveEvent | LiveCrew | LiveRoom | LiveRoomMessage, Field(discriminator="type")]


class NewTask(BaseModel):
    bot: str
    goal: str
    workspace: str | None = None


class Message(BaseModel):
    text: str = ""
    attachments: list[str] = []  # ids from POST /attachments


class AttachmentUpload(BaseModel):
    name: str
    data: str  # base64


class AttachmentCopy(BaseModel):
    path: str  # a file you picked


class AttachmentData(BaseModel):
    name: str
    mime: str
    data: str  # base64


class NewRoom(BaseModel):
    title: str = ""
    members: list[str]
    lead: str | None = None


class Delegation(BaseModel):
    bot: str
    goal: str
    workspace: str | None = None
    review_of: str | None = None


class ChildMessage(BaseModel):
    task_id: str
    text: str


class HandoffBody(BaseModel):
    done: str
    output: list[str] = []
    open: str = ""
    needs: str = ""


class Escalation(BaseModel):
    question: str


class Finding(BaseModel):
    location: str
    problem: str
    scenario: str = ""


class ReviewBody(BaseModel):
    verdict: Verdict
    summary: str
    findings: list[Finding] = []


class Remember(BaseModel):
    title: str
    body: str
    shared: bool = False


class Forget(BaseModel):
    name: str
    shared: bool = False


class ThreadBody(BaseModel):
    title: str
    goal: str


class PostBody(BaseModel):
    text: str
    room: str | None = None


class Recall(BaseModel):
    query: str


class SearchHit(BaseModel):
    task_id: str
    kind: str
    at: str
    snippet: str
    bot: str
    title: str


class MergeProposal(BaseModel):
    task_id: str
    summary: str


class MergeRequest(BaseModel):
    force: bool = False


def _warm_inbox(reviews: Reviews) -> None:
    with contextlib.suppress(Exception):  # the app asks again, and shows GitHub's error then
        reviews.inbox()


def create_app(runner: Runner, token: str, port: int = DEFAULT_PORT, scheduler: Scheduler | None = None,
               reviews: Reviews | None = None) -> FastAPI:
    store = runner.store
    scheduler = scheduler or Scheduler(runner)
    reviews = reviews or Reviews(runner.crew.home)
    def review_tools(task: Task) -> ReviewTools | None:
        """A review's tools: the pull request for every reviewer, drafting for the lead (the one with no parent), and
        reading the repository on GitHub only when it couldn't be checked out."""
        if not task.pr:
            return None
        return ReviewTools(reviews, task.pr, drafts=store, task_id=task.id, author=task.bot,
                           reads_github=reviews.is_snapshot(task.workspace), drafting=task.parent_id is None)

    runner.review_tools = review_tools
    starting: set[asyncio.Task] = set()  # reviews getting their code, kept until they've started
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def allowed(host: str | None, credential: str | None) -> bool:
        return host in hosts and credential is not None and secrets.compare_digest(credential, token)

    app = FastAPI(title="crewbots", version=str(API_VERSION), openapi_url=None, docs_url=None, redoc_url=None)
    app.state.reviews = reviews
    app.state.restart = None  # the restart waiting for the crew to be idle
    app.state.stop = lambda code: None  # serve() replaces it: stops the server, which exits with `code`

    @app.middleware("http")
    async def guard(request: Request, call_next):
        auth = request.headers.get("authorization", "")
        credential = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else None
        if request.url.path.startswith("/v1/bot/"):
            task = runner.task_for_token(credential) if credential and request.headers.get("host") in hosts else None
            if task is None:
                return JSONResponse({"detail": "forbidden"}, status_code=403)
            request.state.task = task
        elif not allowed(request.headers.get("host"), credential):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(NotAllowed)
    async def not_allowed(request: Request, e: NotAllowed):
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.exception_handler(MemoryError)
    async def memory_error(request: Request, e: MemoryError):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(RoutineError)
    async def routine_error(request: Request, e: RoutineError):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(crew_module.CrewError)
    async def crew_error(request: Request, e: crew_module.CrewError):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(MergeError)
    async def merge_error(request: Request, e: MergeError):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(UnknownBot)
    async def unknown_bot(request: Request, e: UnknownBot):
        return JSONResponse({"detail": str(e.args[0])}, status_code=422)

    v1 = APIRouter(prefix="/v1")

    def task_or_404(task_id: str):
        try:
            return store.get_task(task_id)
        except KeyError:
            raise HTTPException(404, f"no task {task_id}") from None

    @v1.get("/bots", response_model=list[BotOut])
    async def bots():
        return [
            {"name": b.name, "title": b.title, "description": b.description, "backend": b.backend, "model": b.model,
             "access": b.access, "workspace": str(b.workspace) if b.workspace else None,
             "network": list(b.network) if isinstance(b.network, tuple) else b.network, "mcp": sorted(b.mcp),
             "skills": list(b.skills), "can_delegate": b.can_delegate, "path": str(b.path) if b.path else None,
             "display_name": b.display_name, "avatar": b.avatar, "effort": b.effort,
             "context_window": b.context_window}
            for b in runner.crew.bots.values()
        ]

    # Editing the crew from the app. Each change is checked by the crew's own parser before the file is replaced.

    def edited(fields: dict[str, Any]) -> dict[str, Any]:
        if fields.get("avatar") is not None:
            fields["avatar"] = {k: v for k, v in fields["avatar"].items() if v}
        return fields

    def reload_crew() -> None:
        runner.reload(crew_module.load(runner.crew.home))

    @v1.get("/bots/{name}/card", response_model=CardOut)
    async def get_card(name: str):
        try:
            return cards.card(runner.crew.home, name)
        except KeyError:
            raise HTTPException(404, f"no bot named {name}") from None

    @v1.put("/bots/{name}/card", response_model=CardOut)
    async def put_card(name: str, body: CardBody):
        fields = edited(body.model_dump(exclude_unset=True, exclude={"instructions"}))
        if fields.get("mcp") is not None:  # servers defined on the card move to the crew's list, kept by name
            with contextlib.suppress(KeyError, OSError):
                inline = cards.read(cards._bot_path(runner.crew.home, name))[0].get("mcp")
                for server in fields["mcp"]:
                    if isinstance(inline, dict) and server in inline and server not in mcp_catalog.servers(runner.crew.home):
                        mcp_catalog.save(runner.crew.home, server, inline[server])
        try:
            cards.save_card(runner.crew.home, name, fields, body.instructions)
        except KeyError:
            raise HTTPException(404, f"no bot named {name}") from None
        reload_crew()
        return cards.card(runner.crew.home, name)

    # The crew's MCP servers.

    def mcp_out(name: str, spec: dict[str, Any]) -> dict[str, Any]:
        users = sorted(b.name for b in runner.crew.bots.values() if name in b.mcp_names)
        return mcp_catalog.summary(name, spec, users)

    @v1.get("/mcp/servers", response_model=list[McpServerOut])
    async def mcp_servers():
        return [mcp_out(n, s) for n, s in sorted(mcp_catalog.servers(runner.crew.home).items())]

    @v1.put("/mcp/servers/{name}", response_model=McpServerOut)
    async def put_mcp_server(name: str, body: McpServerBody):
        """Adds or changes a server. Left out, `env` and `headers` keep what the server had."""
        old = mcp_catalog.servers(runner.crew.home).get(name) or {}
        if body.transport == "stdio":
            spec = {"command": body.command or "", "args": body.args,
                    "env": body.env if body.env is not None else old.get("env") or {}}
        else:
            spec = {"type": body.transport, "url": body.url or "",
                    "headers": body.headers if body.headers is not None else old.get("headers") or {}}
        mcp_catalog.save(runner.crew.home, name, {k: v for k, v in spec.items() if v not in ({}, [])})
        reload_crew()
        return mcp_out(name, mcp_catalog.servers(runner.crew.home)[name])

    @v1.delete("/mcp/servers/{name}")
    async def delete_mcp_server(name: str):
        """Removes a server, and takes it off every role card that names it."""
        bots = mcp_catalog.remove(runner.crew.home, name)
        reload_crew()
        return {"removed": name, "bots": bots}

    @v1.post("/mcp/servers/{name}/check", response_model=McpCheck)
    async def check_mcp_server(name: str):
        """Starts or reaches the server and lists its tools."""
        spec = mcp_catalog.servers(runner.crew.home).get(name)
        if spec is None:
            raise HTTPException(404, f"there's no MCP server called {name}")
        return await mcp_catalog.check(crew_module.mcp_server(name, spec, "mcp.json"))

    @v1.get("/skills", response_model=list[SkillOut])
    async def skills():
        """Every skill the bots can use: the crew's own, then your global ones."""
        found = runner.crew.skills
        return [{"name": s.name, "description": s.description, "source": s.source, "path": str(s.path), "used": True,
                 "same_as": None} for s in found.values()] + [
            {"name": s.name, "description": s.description, "source": s.source, "path": str(s.path), "used": False,
             "same_as": found[s.name].source if s.name in found else None} for s in runner.crew.shadowed_skills]

    @v1.get("/mcp/installed", response_model=list[InstalledServer])
    async def installed_mcp():
        """The servers your other apps have: Claude Code, Claude's desktop app and Codex."""
        crew_servers = mcp_catalog.servers(runner.crew.home)
        return [mcp_catalog.summary(f["name"], f["spec"], []) | {"id": f["id"], "source": f["source"],
                                                                  "in_crew": f["name"] in crew_servers}
                for f in await asyncio.to_thread(mcp_catalog.installed)]

    @v1.post("/mcp/import", response_model=McpServerOut, status_code=201)
    async def import_mcp(body: ImportBody):
        """Copies a server from one of your apps into the crew, env and all."""
        found = next((f for f in await asyncio.to_thread(mcp_catalog.installed) if f["id"] == body.id), None)
        if found is None:
            raise HTTPException(404, "that server isn't in your apps' configs any more")
        name = body.name or found["name"]
        mcp_catalog.save(runner.crew.home, name, found["spec"])
        reload_crew()
        return mcp_out(name, found["spec"])

    @v1.get("/mcp/registry", response_model=list[RegistryEntry])
    async def search_mcp_registry(q: str = Query(..., min_length=1)):
        """The official MCP registry's servers with `q` in their name."""
        try:
            return await mcp_catalog.search(q)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"the MCP registry didn't answer: {e}") from None

    @v1.post("/mcp/registry", response_model=McpServerOut, status_code=201)
    async def add_from_registry(body: RegistryAdd):
        """Adds one way a registry server runs (a package to start, or its address), with the values filled in."""
        try:
            entry = await mcp_catalog.fetch(body.server, body.version)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"the MCP registry didn't answer: {e}") from None
        spec = mcp_catalog.from_registry(entry, body.option, body.values)
        mcp_catalog.save(runner.crew.home, body.name, spec)
        reload_crew()
        return mcp_out(body.name, spec)

    @v1.post("/bots", status_code=201, response_model=CardOut)
    async def new_bot(body: NewBot):
        fields = edited(body.model_dump(exclude_unset=True, exclude={"instructions", "name"}))
        cards.save_card(runner.crew.home, body.name, fields, body.instructions or "", create=True)
        reload_crew()
        return cards.card(runner.crew.home, body.name)

    @v1.get("/tasks", response_model=list[TaskOut])
    async def tasks(status: Status | None = None, bot: str | None = None, limit: int = Query(100, le=1000)):
        return [t.to_json() for t in store.list_tasks(status=status, bot=bot, limit=limit)]

    @v1.post("/tasks", status_code=201, response_model=TaskOut)
    async def new_task(body: NewTask):
        return runner.submit(body.bot, body.goal, workspace=body.workspace).to_json()

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

    def attached(body: Message) -> list[dict[str, Any]]:
        if not body.text.strip() and not body.attachments:
            raise HTTPException(422, "a message needs text, or an attachment")
        try:
            return [attachments_module.find(runner.crew.home, a) for a in body.attachments]
        except attachments_module.AttachmentError as e:
            raise HTTPException(422, str(e)) from None

    @v1.post("/tasks/{task_id}/messages", response_model=TaskOut)
    async def message(task_id: str, body: Message):
        task_or_404(task_id)
        return runner.send(task_id, body.text, attachments=attached(body)).to_json()

    @v1.post("/attachments", response_model=AttachmentOut, status_code=201)
    async def upload_attachment(body: AttachmentUpload):
        """Keeps a file sent from the app (a pasted image, a dropped file), for a message to carry."""
        try:
            return attachments_module.save(runner.crew.home, body.name, base64.b64decode(body.data))
        except (attachments_module.AttachmentError, binascii.Error) as e:
            raise HTTPException(422, str(e)) from None

    @v1.post("/attachments/copy", response_model=AttachmentOut, status_code=201)
    async def copy_attachment(body: AttachmentCopy):
        """Keeps a copy of a file you picked."""
        try:
            return await asyncio.to_thread(attachments_module.copy_in, runner.crew.home, Path(body.path))
        except attachments_module.AttachmentError as e:
            raise HTTPException(422, str(e)) from None

    @v1.get("/attachments/{attachment_id}", response_model=AttachmentData)
    async def attachment(attachment_id: str):
        """An attachment's bytes, for the app to show an image."""
        try:
            found = attachments_module.find(runner.crew.home, attachment_id)
        except attachments_module.AttachmentError as e:
            raise HTTPException(404, str(e)) from None
        data = await asyncio.to_thread(Path(found["path"]).read_bytes)
        return {"name": found["name"], "mime": found["mime"], "data": base64.b64encode(data).decode()}

    @v1.post("/tasks/{task_id}/cancel", response_model=TaskOut)
    async def cancel(task_id: str):
        task_or_404(task_id)
        return (await runner.cancel(task_id)).to_json()

    @v1.get("/tasks/{task_id}/changes", response_model=ChangesOut)
    async def changes(task_id: str):
        task_or_404(task_id)
        return {"diff": runner.changes(task_id)}

    # Chats: a direct one with each bot, and the group chats.

    def busy(task_id: str) -> bool:
        return any(t.active or t.status is Status.HELD or t.id in runner.running() for t in store.tree(task_id))

    def direct(bot: str) -> dict[str, Any]:
        chat = runner.chat(bot)
        said = store.last_event(chat.id, ("text",))
        asked = store.last_event(chat.id, ("message",))
        last = None
        if asked and asked.data.get("from") not in ("crewbots", "room") and (not said or asked.id > said.id):
            last = {"author": asked.data.get("from", "user"), "text": asked.data.get("text", ""), "at": asked.at}
        elif said:
            last = {"author": bot, "text": said.data.get("text", ""), "at": said.at}
        unread = store.count_events_after(chat.id, store.seen(f"chat:{chat.id}"), ("text", "escalation"))
        return {"key": f"bot:{bot}", "kind": "direct", "title": runner.crew.bots[bot].title, "bot": bot,
                "task_id": chat.id, "room": None, "last": last, "unread": unread, "working": busy(chat.id),
                "updated_at": last["at"] if last else chat.created_at}

    def group(room: dict[str, Any]) -> dict[str, Any]:
        message = store.last_room_message(room["id"])
        seen = store.seen(f"room:{room['id']}")
        unread = sum(1 for m in store.room_messages(room["id"], after=seen) if m["author"] != "user")
        seats = [store.seat_of(room["id"], b) for b in room["members"]]
        return {"key": f"room:{room['id']}", "kind": "group", "title": room["title"], "bot": None, "task_id": None,
                "room": room, "last": {k: message[k] for k in ("author", "text", "at")} if message else None,
                "unread": unread, "working": any(s and busy(s.id) for s in seats), "updated_at": room["updated_at"]}

    def room_or_404(room_id: str) -> dict[str, Any]:
        try:
            return store.room(room_id)
        except KeyError:
            raise HTTPException(404, f"no group chat {room_id}") from None

    @v1.get("/chats", response_model=list[ChatOut])
    async def chats():
        """Every conversation: one with each bot, then the group chats (the crew's own is made the first time)."""
        if runner.crew.bots:
            runner.crew_room()
        return [direct(b) for b in runner.crew.bots] + [group(r) for r in store.rooms()]

    @v1.post("/chats/bot/{bot}/messages", response_model=TaskOut)
    async def message_bot(bot: str, body: Message):
        return runner.say(bot, body.text, attachments=attached(body)).to_json()

    @v1.post("/chats/{key}/read")
    async def read(key: str):
        """Marks a conversation seen up to now."""
        kind, _, name = key.partition(":")
        if kind == "bot":
            chat = runner.chat(name)
            store.mark_seen(f"chat:{chat.id}", store.latest_event_id())
        elif kind == "room":
            room_or_404(name)
            last = store.last_room_message(name)
            store.mark_seen(f"room:{name}", last["id"] if last else 0)
        else:
            raise HTTPException(404, f"no conversation {key}")
        return {"ok": True}

    @v1.post("/rooms", status_code=201, response_model=RoomOut)
    async def new_room(body: NewRoom):
        return runner.create_room(body.title, body.members, body.lead)

    @v1.get("/rooms/{room_id}/messages", response_model=list[RoomMessageOut])
    async def room_messages(room_id: str, after: int = 0, limit: int = Query(1000, le=10_000)):
        room_or_404(room_id)
        return store.room_messages(room_id, after=after, limit=limit)

    @v1.post("/rooms/{room_id}/messages", status_code=201, response_model=RoomMessageOut)
    async def post_message(room_id: str, body: Message):
        room_or_404(room_id)
        return runner.post(room_id, "user", body.text, attachments=attached(body))

    @v1.get("/bots/{bot}/profile", response_model=ProfileOut)
    async def profile(bot: str):
        """What a bot is doing and has done: its threads, newest first, and the files they handed back."""
        if bot not in runner.crew.bots:
            raise HTTPException(404, f"no bot named {bot}")
        threads = store.threads_of(bot)
        outputs = []
        for thread in threads:
            handoff = store.latest_handoff(thread.id)
            outputs += [{"path": p, "task_id": thread.id, "title": thread.title, "at": handoff.at}
                        for p in (handoff.output if handoff else [])]
        chat = runner.chat(bot)
        mine = [chat, *threads, *(s for r in store.rooms() if (s := store.seat_of(r["id"], bot)))]
        return {"bot": bot, "working": any(t.id in runner.running() and t.id not in runner.held() for t in mine),
                "last_active": max((t.updated_at for t in mine), default=None),
                "threads": [t.to_json() for t in threads], "outputs": outputs[:30]}

    # Code review: pull requests waiting on you, and private reviews of them. Nothing is posted to GitHub.

    def reviewing(fn, *args):
        try:
            return fn(*args)
        except (GitHubError, ReviewError) as e:
            raise HTTPException(502 if isinstance(e, GitHubError) else 422, str(e)) from None

    @v1.get("/reviews/inbox", response_model=Inbox)
    async def review_inbox(lists: str = ",".join(INBOX_LISTS), fresh: bool = False):
        """`lists`: any of assigned, direct, team and mine. Kept answers come back at once; `fresh` waits for GitHub."""
        asked = tuple(k for k in lists.split(",") if k in SEARCHES) or INBOX_LISTS
        inbox, error = await asyncio.to_thread(reviews.inbox, asked, fresh)
        touched = store.reviewed_prs()
        return {k: [p | {"reviewed_at": touched.get(f"{p['repo']}#{p['number']}")} for p in v] for k, v in inbox.items()} | {
            "error": error}

    # Review comments and submitting a review: the user's token only. A reviewer drafts through its own tool, and
    # nothing a bot can reach submits or posts.

    @v1.get("/reviews/{owner}/{name}/{number}/drafts", response_model=list[DraftOut])
    async def review_drafts(owner: str, name: str, number: int):
        return store.drafts(f"{owner}/{name}", number)

    @v1.post("/reviews/{owner}/{name}/{number}/drafts", response_model=DraftOut, status_code=201)
    async def add_review_draft(owner: str, name: str, number: int, body: DraftBody):
        repo = f"{owner}/{name}"
        spot = await asyncio.to_thread(reviewing, reviews.check_draft, repo, number, body.path, body.line, body.side)
        return store.add_draft(repo, number, spot["path"], spot["line"], spot["side"], body.body, "user",
                               commit_sha=spot["commit_sha"])

    @v1.put("/reviews/drafts/{draft_id}", response_model=DraftOut)
    async def edit_review_draft(draft_id: int, body: DraftEdit):
        try:
            return store.edit_draft(draft_id, body.body)
        except KeyError:
            raise HTTPException(404, "that draft is gone") from None

    @v1.delete("/reviews/drafts/{draft_id}")
    async def delete_review_draft(draft_id: int):
        store.delete_drafts([draft_id])
        return {"deleted": draft_id}

    @v1.post("/reviews/{owner}/{name}/{number}/submit", response_model=Submitted)
    async def submit_review(owner: str, name: str, number: int, body: SubmitReview):
        """Posts the user's review to GitHub, approving, requesting changes or commenting, with the drafts chosen."""
        repo = f"{owner}/{name}"
        drafts = [d for d in store.drafts(repo, number) if body.drafts is None or d["id"] in body.drafts]
        done = await asyncio.to_thread(reviewing, reviews.submit, repo, number, body.event, body.body, drafts)
        store.delete_drafts([d["id"] for d in drafts])
        return done

    @v1.get("/reviews/{owner}/{name}/{number}/image", response_model=ImageOut)
    async def review_image(owner: str, name: str, number: int, path: str, side: Literal["head", "base"] = "head"):
        """An image the pull request adds or changes, for the diff to show."""
        return await asyncio.to_thread(reviewing, reviews.image, f"{owner}/{name}", number, path, side)

    @v1.get("/reviews/{owner}/{name}/{number}", response_model=PullOut)
    async def review_pull(owner: str, name: str, number: int):
        repo = f"{owner}/{name}"
        pull = await asyncio.to_thread(reviewing, reviews.pull, repo, number)
        return pull | {"reviews": [t.to_json() for t in store.reviews_of(f"{repo}#{number}")]}

    @v1.get("/reviews/{owner}/{name}/{number}/diff", response_model=ChangesOut)
    async def review_diff(owner: str, name: str, number: int):
        return {"diff": await asyncio.to_thread(reviewing, reviews.diff, f"{owner}/{name}", number)}

    @v1.post("/reviews/{owner}/{name}/{number}/review", status_code=201, response_model=list[TaskOut])
    async def review_start(owner: str, name: str, number: int, body: ReviewRequest | None = None):
        """A private review: each of your reviewer models reviews the pull request on its own, then the first, as the
        lead, puts their findings together into one review, with its line comments drafted. Returns the lead, whose
        conversation it is; the reviewers start once the code is checked out, which the first review of a
        repository takes about a minute for."""
        repo, note = f"{owner}/{name}", body.note if body else ""
        pr = await asyncio.to_thread(reviewing, reviews.pull, repo, number)
        names = [reviewer_name(r) for r in reviews.reviewers()]
        lead = runner.start_review(names[0], title=f"Review {repo}#{number}: {pr['title']}", pr=f"{repo}#{number}",
                                   request=reviews.request(repo, number, note))

        async def begin() -> None:
            runner.note(lead.id, f"Getting {repo}'s code. The first review of a repository takes about a minute; "
                                 "later ones, a few seconds.")
            try:
                folder, whole = await asyncio.to_thread(reviews.checkout, repo, number)
                goal = await asyncio.to_thread(reviews.goal, repo, number, note, whole)
            except (GitHubError, ReviewError) as e:
                runner.fail(lead.id, f"couldn't get the pull request's code: {e}")
                return
            runner.start_reviewers(lead.id, names, goal, str(folder))

        starting.add(task := asyncio.create_task(begin()))
        task.add_done_callback(starting.discard)
        return [lead.to_json()]

    @v1.get("/reviews/reviewers", response_model=list[Reviewer])
    async def get_reviewers():
        return reviews.reviewers()

    @v1.put("/reviews/reviewers", response_model=list[Reviewer])
    async def put_reviewers(body: list[Reviewer]):
        """The models that review a pull request, each with its reasoning effort."""
        return reviewing(reviews.set_reviewers, [r.model_dump() for r in body])

    @v1.get("/models", response_model=ModelsOut)
    async def list_models(fresh: bool = False):
        """The models each CLI offers, with the reasoning efforts each takes, as the CLIs report them."""
        return await models_module.models(fresh)

    @v1.get("/reviews/repos", response_model=dict[str, str])
    async def review_repos():
        return reviews.repos()

    @v1.put("/reviews/repos/{owner}/{name}", response_model=dict[str, str])
    async def review_repo(owner: str, name: str, body: RepoFolder):
        """Maps a repo to your local checkout of it, so reviews read the code, not just the diff."""
        return reviewing(reviews.set_repo, f"{owner}/{name}", body.folder)

    @v1.get("/usage/summary", response_model=UsageSummary)
    async def usage_summary(days: int = Query(371, ge=7, le=731)):
        """The crew's use over the last `days`: a year and a week by default, for the activity grid."""
        return await asyncio.to_thread(usage_module.summary, store, runner.bot_for, days)

    @v1.get("/plans", response_model=PlansOut)
    async def get_plans(fresh: bool = False):
        """How much of each plan's limits is used, and when they reset, asked of the CLIs. Kept answers come back at
        once (`checked_at` says how old they are); `fresh` waits for new ones."""
        return await plans_module.plans(fresh)

    @v1.get("/search", response_model=list[SearchHit])
    async def search(q: str, limit: int = Query(20, le=200)):
        """Everything the crew said or decided that matches `q`."""
        return store.search(q, limit=limit)

    @v1.get("/crew", response_model=CrewState)
    async def crew_state():
        return runner.state()

    @v1.post("/crew/pause", response_model=CrewState)
    async def pause():
        """No new turn starts until resumed; turns already running finish."""
        runner.pause()
        return runner.state()

    @v1.post("/crew/resume", response_model=CrewState)
    async def resume():
        runner.resume()
        return runner.state()

    started_from = _commit()
    reported: deque[float] = deque()  # when the app last reported errors, so a part failing in a loop can't flood

    @v1.post("/client-errors", status_code=204)
    async def client_error(body: ClientError):
        now = time.monotonic()
        while reported and now - reported[0] > 60:
            reported.popleft()
        if len(reported) < CLIENT_ERRORS_PER_MINUTE:
            reported.append(now)
            stack = (body.stack or "")[:4000]
            print(f"app: {body.where} failed: {body.message[:500]}" + (f"\n{stack}" if stack else ""), file=sys.stderr,
                  flush=True)

    @v1.get("/version", response_model=VersionOut)
    async def version():
        now = await asyncio.to_thread(_commit)
        return {"api": API_VERSION, "schema_hash": typescript.schema_hash(app.openapi()), "build": started_from,
                "source_changed": bool(started_from and now and now != started_from)}

    @v1.post("/crew/restart", response_model=CrewState)
    async def restart(body: RestartBody):
        """Restarts the daemon once no turn is running: none starts meanwhile. After `timeout_s` (or at once, unless
        `when_idle`), turns still running are cut off, and carry on by themselves when it starts again."""
        runner.restart_when_idle()
        if app.state.restart is None or app.state.restart.done():
            async def restart_after() -> None:
                await runner.idle(body.timeout_s if body.when_idle else 0)
                app.state.stop(RESTART_EXIT)

            app.state.restart = asyncio.create_task(restart_after(), name="restart when idle")
        return runner.state()

    @v1.delete("/crew/restart", response_model=CrewState)
    async def cancel_restart():
        if app.state.restart is not None:
            app.state.restart.cancel()
            app.state.restart = None
        runner.cancel_restart()
        return runner.state()

    @v1.post("/crew/reload", response_model=CrewState)
    async def reload():
        """Reads the crew's folder again: edited role cards and budgets count from the next turn."""
        runner.reload(crew_module.load(runner.crew.home))
        return runner.state()

    @v1.get("/routines", response_model=RoutinesOut)
    async def routines():
        scheduler.reload_if_changed()
        scheduler.load()
        have = {r.name for r in scheduler.routines} | {Path(f).stem for f in scheduler.errors}
        starters = []
        for name, text in sorted(routines_module.starters().items()):
            if name not in have:
                starter = routines_module.parse(text, name, Path(name))
                starters.append({"name": name, "description": starter.description, "when": starter.when})
        return {"routines": [_routine_out(v) for v in scheduler.views()], "errors": scheduler.errors,
                "github_error": scheduler.github_error, "crew_error": scheduler.crew_error, "starters": starters}

    @v1.get("/routines/{name}/file", response_model=RoutineFileOut)
    async def get_routine(name: str):
        try:
            return cards.routine(runner.crew.home, name)
        except KeyError:
            raise HTTPException(404, f"no routine named {name}") from None

    @v1.put("/routines/{name}/file", response_model=RoutineFileOut)
    async def put_routine(name: str, body: RoutineBody):
        try:
            cards.save_routine(runner.crew.home, name, body.model_dump(exclude_unset=True, exclude={"goal"}), body.goal)
        except KeyError:
            raise HTTPException(404, f"no routine named {name}") from None
        scheduler.load()
        return cards.routine(runner.crew.home, name)

    @v1.post("/routines", status_code=201, response_model=RoutineFileOut)
    async def new_routine(body: NewRoutine):
        cards.save_routine(runner.crew.home, body.name, body.model_dump(exclude_unset=True, exclude={"goal", "name"}),
                           body.goal or "", create=True)
        scheduler.load()
        return cards.routine(runner.crew.home, body.name)

    @v1.delete("/routines/{name}")
    async def delete_routine(name: str):
        try:
            cards.delete_routine(runner.crew.home, name)
        except KeyError:
            raise HTTPException(404, f"no routine named {name}") from None
        scheduler.load()
        return {"ok": True}

    @v1.post("/routines/starters/{name}", status_code=201, response_model=RoutineOut)
    async def add_starter(name: str):
        """Copies a starter routine into the crew's routines/ folder."""
        routines_module.add_starter(runner.crew.home, name)
        scheduler.load()
        return _routine_out(next(v for v in scheduler.views() if v.routine.name == name))

    @v1.post("/routines/{name}/run", status_code=201, response_model=TaskOut)
    async def run_routine(name: str):
        """Runs a scheduled routine now, as if its time had come."""
        return scheduler.run_now(name).to_json()

    @v1.get("/budgets", response_model=list[BudgetOut])
    async def budgets():
        return runner.budgets()

    @v1.post("/tasks/{task_id}/release", response_model=TaskOut)
    async def release(task_id: str):
        """Lets a task held on a budget or plan limit go ahead, with the rest of its goal."""
        task_or_404(task_id)
        return runner.release(task_id).to_json()

    @v1.get("/events/recent", response_model=list[EventOut])
    async def recent_events(limit: int = Query(200, le=2000), kinds: str | None = None):
        """The crew's newest events, oldest first; `kinds` is a comma-separated filter (e.g. handoff,review)."""
        wanted = tuple(k for k in kinds.split(",") if k) if kinds else None
        return [e.to_json() for e in store.recent_events(limit, wanted)]

    @v1.get("/events/cursor", response_model=CursorOut)
    async def events_cursor():
        """The newest event's id, so a client can follow /live from now on instead of replaying the history."""
        return {"id": store.latest_event_id()}

    @v1.get("/tasks/{task_id}/children", response_model=list[ChildOut])
    async def task_children(task_id: str):
        task_or_404(task_id)
        return runner.children_of(task_id)

    @v1.get("/tasks/{task_id}/review", response_model=ReviewStateOut)
    async def task_review(task_id: str):
        task_or_404(task_id)
        return runner.review_state(task_id).to_json()

    @v1.post("/tasks/{task_id}/merge", response_model=TaskOut)
    async def merge(task_id: str, body: MergeRequest | None = None):
        task_or_404(task_id)
        return runner.merge(task_id, force=bool(body and body.force)).to_json()

    # The bot side: what a bot's crewbots MCP server calls, as the task its run token belongs to.

    @v1.post("/bot/delegate", status_code=201)
    async def bot_delegate(request: Request, body: Delegation):
        child = runner.delegate(request.state.task.id, body.bot, body.goal, workspace=body.workspace,
                                review_of=body.review_of)
        return {"id": child.id, "bot": child.bot, "title": child.title, "status": child.status}

    @v1.get("/bot/tasks")
    async def bot_tasks(request: Request):
        return runner.children_of(request.state.task.id)

    @v1.post("/bot/message")
    async def bot_message(request: Request, body: ChildMessage):
        task = runner.message_child(request.state.task.id, body.task_id, body.text)
        return {"id": task.id, "status": task.status}

    @v1.post("/bot/handoff", status_code=201)
    async def bot_handoff(request: Request, body: HandoffBody):
        return runner.handoff(request.state.task.id, body.done, body.output, body.open, body.needs).to_json()

    @v1.post("/bot/review", status_code=201)
    async def bot_review(request: Request, body: ReviewBody):
        findings = [f.model_dump() for f in body.findings]
        return runner.review(request.state.task.id, body.verdict, body.summary, findings).to_json()

    @v1.post("/bot/propose_merge")
    async def bot_propose_merge(request: Request, body: MergeProposal):
        task = runner.propose_merge(request.state.task.id, body.task_id, body.summary)
        return {"id": task.id, "merge_state": task.merge_state}

    @v1.post("/bot/remember", status_code=201)
    async def bot_remember(request: Request, body: Remember):
        note = runner.remember(request.state.task.id, body.title, body.body, shared=body.shared)
        return {"title": note.title, "path": str(note.path)}

    @v1.post("/bot/forget")
    async def bot_forget(request: Request, body: Forget):
        note = runner.forget(request.state.task.id, body.name, shared=body.shared)
        return {"title": note.title}

    @v1.post("/bot/recall", response_model=list[SearchHit])
    async def bot_recall(request: Request, body: Recall):
        return runner.recall(request.state.task.id, body.query)

    @v1.post("/bot/start_thread", status_code=201)
    async def bot_start_thread(request: Request, body: ThreadBody):
        thread = runner.start_thread(request.state.task.id, body.title, body.goal)
        return {"id": thread.id, "bot": thread.bot, "title": thread.title, "status": thread.status}

    @v1.post("/bot/add_bot", status_code=201)
    async def bot_add_bot(request: Request, body: dict[str, Any]):
        try:
            bot = runner.add_bot(request.state.task.id, body)
        except (KeyError, ValueError) as e:  # a missing field, or an access that isn't one
            raise HTTPException(422, f"add_bot: {e}") from None
        return {"name": bot.name, "label": bot.label, "title": bot.title}

    @v1.post("/bot/post", status_code=201)
    async def bot_post(request: Request, body: PostBody):
        return runner.post_from(request.state.task.id, body.text, body.room or None)

    @v1.post("/bot/review/{tool}")
    async def bot_review_tool(request: Request, tool: str, body: dict[str, Any]):
        """A reviewer's tools, for one that reaches crewbots over MCP. It can only read and draft on its own pull
        request; nothing here posts to GitHub."""
        task = request.state.task
        if task.kind != "review" or not task.pr:
            raise HTTPException(403, "only a code review has these tools")
        try:
            text = await review_tools(task).call(tool, {k: v for k, v in body.items() if v is not None})
        except ToolRefused as e:
            raise HTTPException(422, str(e)) from None
        return {"text": text}

    @v1.post("/bot/escalate")
    async def bot_escalate(request: Request, body: Escalation):
        runner.escalate(request.state.task.id, body.question)
        return {"ok": True}

    @v1.websocket("/live")
    async def live(ws: WebSocket, after: int = 0, token_param: str | None = Query(None, alias="token")):
        """Every event after `after`, then everything new: task updates and events, as they happen.

        Browsers cannot set headers on a WebSocket, so the token may come as ?token= instead.
        """
        auth = ws.headers.get("authorization", "")
        credential = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else token_param
        if not allowed(ws.headers.get("host"), credential):
            await ws.close(code=4403)
            return
        await ws.accept()
        with runner.bus.subscribe() as queue:  # subscribe first, so nothing falls between the backlog and live
            last = after
            for event in store.events(after=after, limit=100_000):
                await ws.send_json({"type": "event", "event": event.to_json()})
                last = event.id
            receiver = asyncio.create_task(_until_closed(ws))
            try:
                while not receiver.done():
                    getter = asyncio.create_task(queue.get())
                    finished, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                    if getter not in finished:
                        getter.cancel()
                        break
                    message = getter.result()
                    if message["type"] == "event" and message["event"]["id"] <= last:
                        continue  # already sent in the backlog
                    await ws.send_json(message)
            finally:
                receiver.cancel()

    @v1.get("/live/schema", response_model=LiveMessage, include_in_schema=True)
    async def live_schema():
        """Not for calling: it describes the messages /v1/live sends, so they are in the schema the app's types
        are generated from."""
        raise HTTPException(404, "the messages are sent on /v1/live")

    app.include_router(v1)
    return app


def openapi_schema() -> dict[str, Any]:
    """The API's schema, without a crew or a database: what app/src/api-types.ts is generated from."""
    from crewbots.crew import Crew

    runner = Runner(Store(":memory:"), Crew(home=Path("."), bots={}, team_files={}))
    return create_app(runner, token="schema").openapi()


async def _rotate_log_now_and_then() -> None:
    while True:
        if service_module.rotate_log():
            print("started a fresh daemon.log; the last one is daemon.log.1", flush=True)
        await asyncio.sleep(LOG_CHECK_S)


def _commit() -> str | None:
    """The commit the daemon's checkout is at, or None when it isn't one (an installed package)."""
    if not (PROJECT / ".git").exists():
        return None
    try:
        done = subprocess.run(["git", "-C", str(PROJECT), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                              text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None


def _routine_out(view: Any) -> dict[str, Any]:
    r = view.routine
    return {"name": r.name, "description": r.description, "bot": r.bot, "when": r.when,
            "schedule": r.schedule.text if r.schedule else None, "on": r.on, "repo": r.repo,
            "workspace": str(r.workspace) if r.workspace else None, "budget": r.budget, "enabled": r.enabled,
            "path": str(r.path), "last_run_at": view.last_run_at, "last_task_id": view.last_task_id,
            "next_run_at": view.next_run_at, "skipped_at": view.skipped_at}


async def _until_closed(ws: WebSocket) -> None:
    with contextlib.suppress(Exception):
        while True:
            await ws.receive_text()


@dataclass(frozen=True)
class DaemonInfo:
    url: str
    token: str
    pid: int


def read_daemon_info(home: Path) -> DaemonInfo:
    path = home.expanduser() / DAEMON_FILE
    if not path.exists():
        raise FileNotFoundError(f"no daemon running for {home} (start one with `crewbots serve`)")
    return DaemonInfo(**json.loads(path.read_text()))


def serve(home: Path, port: int = DEFAULT_PORT, max_parallel: int = 4, sessions: bool = True) -> None:
    home = home.expanduser()
    crew = crew_module.load(home)
    store = Store(home / "crewbots.db")
    store.mark_interrupted()
    token = secrets.token_urlsafe(32)
    info_path = home / DAEMON_FILE

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if any(b.backend == "codex" for b in crew.bots.values()):
            # Listing Codex's MCP servers (to keep bots away from them) takes seconds; do it before any bot needs it.
            asyncio.get_running_loop().run_in_executor(None, codex_isolation.warm)
        fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)  # in case an older file was readable by others
        with os.fdopen(fd, "w") as f:
            json.dump({"url": f"http://127.0.0.1:{port}", "token": token, "pid": os.getpid()}, f)
        if resumed := runner.resume_interrupted():
            print(f"carrying on {len(resumed)} turn(s) the last stop cut off", flush=True)
        ticking = asyncio.create_task(scheduler.run(), name="scheduler")
        rotating = asyncio.create_task(_rotate_log_now_and_then(), name="log rotation")
        warming = asyncio.gather(  # the CLIs and GitHub take seconds: ask before the app does
            plans_module.plans(), asyncio.to_thread(_warm_inbox, app.state.reviews))
        try:
            yield
        finally:
            ticking.cancel()
            rotating.cancel()
            warming.cancel()
            await runner.shutdown()
            info_path.unlink(missing_ok=True)
            store.close()

    keep_awake = ("caffeinate", "-i") if sys.platform == "darwin" else None
    runner = Runner(store, crew, max_parallel=max_parallel, api_url=f"http://127.0.0.1:{port}/v1", sessions=sessions,
                    keep_awake=keep_awake)
    scheduler = Scheduler(runner)
    app = create_app(runner, token, port, scheduler)
    app.router.lifespan_context = lifespan
    print(f"crewbots: {len(crew.bots)} bots ({', '.join(crew.bots)}) from {home}, on http://127.0.0.1:{port}", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    exit_code = 0

    def stop(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        server.should_exit = True

    app.state.stop = stop
    server.run()
    if exit_code:
        # Not a clean stop, so launchd starts it again (KeepAlive: SuccessfulExit false), as the app does its own.
        print("restarting", flush=True)
        sys.exit(exit_code)
