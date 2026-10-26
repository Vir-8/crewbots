"""The daemon's API: HTTP for commands, one WebSocket for live updates. The desktop app and `crewbots` both use it.

It listens on localhost only, and every request needs a token. A browser tab can reach localhost too, so the Host
header is checked as well (a DNS-rebinding page arrives with its own host name).

Every route is under /v1. There are two kinds of token. The daemon's token, in daemon.json, is the user's and opens
everything but /v1/bot/. A run token is minted for one run of one task and opens only /v1/bot/, acting as that task:
that is what a bot's crewbots MCP server holds, so a bot can never act for another task, or for the user.
"""

import asyncio
import contextlib
import json
import os
import secrets
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from crewbots import cards
from crewbots import crew as crew_module
from crewbots import models as models_module
from crewbots import routines as routines_module
from crewbots.backends import codex_isolation
from crewbots.crew_tools import ToolRefused
from crewbots.github import GitHubError
from crewbots.memory import MemoryError
from crewbots.reviews import ReviewError, Reviews, ReviewTools, reviewer_name
from crewbots.routines import RoutineError
from crewbots.runner import MergeError, NotAllowed, Runner, UnknownBot
from crewbots.scheduler import Scheduler
from crewbots.store import Status, Store, Verdict

DEFAULT_PORT = 8765
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
    access: Literal["read_only", "workspace_write", "full"] | None = None
    network: bool | list[str] | None = None
    avatar: Avatar | None = None
    budget: Budget | None = None
    instructions: str | None = None  # the card's body: mission, what it owns, what done means


class NewBot(CardBody):
    name: str  # its handle


class CardOut(CardBody):
    name: str


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
    """Pull requests waiting on you, which never include your own, and your own, apart."""

    assigned: list[PullSummary]  # assigned to you
    direct: list[PullSummary]  # asking you for a review
    team: list[PullSummary]  # asking one of your teams
    mine: list[PullSummary]  # yours
    error: str | None


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


class UsageBot(BaseModel):
    bot: str
    by_day: list[int]  # fresh tokens, one per day in `days`
    turns: list[int]
    total: int


class UsageOut(BaseModel):
    days: list[str]  # local dates, oldest first
    bots: list[UsageBot]


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


class CrewState(BaseModel):
    paused: bool
    running: int
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


class RoomMessageOut(BaseModel):
    id: int
    room_id: str
    author: str  # user, or a bot's name
    text: str
    depth: int
    at: str


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
    text: str


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


def create_app(runner: Runner, token: str, port: int = DEFAULT_PORT, scheduler: Scheduler | None = None,
               reviews: Reviews | None = None) -> FastAPI:
    store = runner.store
    scheduler = scheduler or Scheduler(runner)
    reviews = reviews or Reviews(runner.crew.home)
    runner.review_tools = lambda task: (None if not task.pr or reviews.checkout_of(task.pr.rsplit("#", 1)[0])
                                        else ReviewTools(reviews, task.pr))
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def allowed(host: str | None, credential: str | None) -> bool:
        return host in hosts and credential is not None and secrets.compare_digest(credential, token)

    app = FastAPI(title="crewbots", version="1", openapi_url=None, docs_url=None, redoc_url=None)

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
             "display_name": b.display_name, "avatar": b.avatar, "effort": b.effort}
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
        try:
            cards.save_card(runner.crew.home, name, fields, body.instructions)
        except KeyError:
            raise HTTPException(404, f"no bot named {name}") from None
        reload_crew()
        return cards.card(runner.crew.home, name)

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

    @v1.get("/tasks/{task_id}/events", response_model=list[EventOut])
    async def task_events(task_id: str, after: int = 0, limit: int = Query(1000, le=10_000)):
        task_or_404(task_id)
        return [e.to_json() for e in store.events(task_id, after=after, limit=limit)]

    @v1.post("/tasks/{task_id}/messages", response_model=TaskOut)
    async def message(task_id: str, body: Message):
        task_or_404(task_id)
        return runner.send(task_id, body.text).to_json()

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
        return runner.say(bot, body.text).to_json()

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
        return runner.post(room_id, "user", body.text)

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
    async def review_inbox(fresh: bool = False):
        try:
            inbox = await asyncio.to_thread(reviews.inbox, fresh)
        except GitHubError as e:
            return {"assigned": [], "direct": [], "team": [], "mine": [], "error": str(e)}
        touched = store.reviewed_prs()
        return {k: [p | {"reviewed_at": touched.get(f"{p['repo']}#{p['number']}")} for p in v] for k, v in inbox.items()} | {
            "error": None}

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
        """A private review by each of your reviewer models, side by side. Follow-ups go to each task like any
        other."""
        repo = f"{owner}/{name}"
        title, goal = await asyncio.to_thread(reviewing, reviews.brief, repo, number, body.note if body else "")
        folder = await asyncio.to_thread(reviewing, reviews.folder, repo, number)
        return [runner.start_review(reviewer_name(r), goal, title=title, pr=f"{repo}#{number}", folder=folder).to_json()
                for r in reviews.reviewers()]

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

    @v1.get("/usage", response_model=UsageOut)
    async def usage(days: int = Query(14, ge=1, le=90)):
        """Fresh tokens and turns per bot per day, in this Mac's time zone."""
        today = datetime.now().astimezone().date()
        dates = [today - timedelta(days=n) for n in range(days - 1, -1, -1)]
        since = datetime.combine(dates[0], datetime.min.time()).astimezone().astimezone(UTC)
        index = {d.isoformat(): i for i, d in enumerate(dates)}
        rows: dict[str, dict[str, list[int]]] = {
            b: {"by_day": [0] * days, "turns": [0] * days} for b in runner.crew.bots}
        for bot, at, fresh, turns in store.usage_by_day(since.isoformat(timespec="milliseconds")):
            day = datetime.fromisoformat(at).astimezone().date().isoformat()
            if day in index:
                row = rows.setdefault(bot, {"by_day": [0] * days, "turns": [0] * days})
                row["by_day"][index[day]] += fresh
                row["turns"][index[day]] += turns
        return {"days": list(index), "bots": [{"bot": b, **r, "total": sum(r["by_day"])} for b, r in rows.items()]}

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

    @v1.post("/bot/post", status_code=201)
    async def bot_post(request: Request, body: PostBody):
        return runner.post_from(request.state.task.id, body.text, body.room or None)

    @v1.post("/bot/review/{tool}")
    async def bot_review_tool(request: Request, tool: str, body: dict[str, Any]):
        """A reviewer's tools, for one that reaches crewbots over MCP. It can only read its own pull request."""
        task = request.state.task
        if task.kind != "review" or not task.pr:
            raise HTTPException(403, "only a code review has these tools")
        try:
            text = await ReviewTools(reviews, task.pr).call(tool, {k: v for k, v in body.items() if v is not None})
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
    interrupted = store.mark_interrupted()
    if interrupted:
        print(f"marked {len(interrupted)} unfinished task(s) as interrupted; send them a message to resume")
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
        ticking = asyncio.create_task(scheduler.run(), name="scheduler")
        try:
            yield
        finally:
            ticking.cancel()
            await runner.shutdown()
            info_path.unlink(missing_ok=True)
            store.close()

    keep_awake = ("caffeinate", "-i") if sys.platform == "darwin" else None
    runner = Runner(store, crew, max_parallel=max_parallel, api_url=f"http://127.0.0.1:{port}/v1", sessions=sessions,
                    keep_awake=keep_awake)
    scheduler = Scheduler(runner)
    app = create_app(runner, token, port, scheduler)
    app.router.lifespan_context = lifespan
    print(f"crewbots: {len(crew.bots)} bots ({', '.join(crew.bots)}) from {home}, on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
