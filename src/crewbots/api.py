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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from crewbots import crew as crew_module
from crewbots.runner import MergeError, NotAllowed, Runner, UnknownBot
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


class LiveTask(BaseModel):
    type: Literal["task"]
    task: TaskOut


class LiveEvent(BaseModel):
    type: Literal["event"]
    event: EventOut


LiveMessage = Annotated[LiveTask | LiveEvent, Field(discriminator="type")]


class NewTask(BaseModel):
    bot: str
    goal: str
    workspace: str | None = None


class Message(BaseModel):
    text: str


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


class MergeProposal(BaseModel):
    task_id: str
    summary: str


class MergeRequest(BaseModel):
    force: bool = False


def create_app(runner: Runner, token: str, port: int = DEFAULT_PORT) -> FastAPI:
    store = runner.store
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
             "access": b.access, "workspace": str(b.workspace) if b.workspace else None}
            for b in runner.crew.bots.values()
        ]

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


def serve(home: Path, port: int = DEFAULT_PORT, max_parallel: int = 4) -> None:
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
        fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)  # in case an older file was readable by others
        with os.fdopen(fd, "w") as f:
            json.dump({"url": f"http://127.0.0.1:{port}", "token": token, "pid": os.getpid()}, f)
        try:
            yield
        finally:
            await runner.shutdown()
            info_path.unlink(missing_ok=True)
            store.close()

    runner = Runner(store, crew, max_parallel=max_parallel, api_url=f"http://127.0.0.1:{port}/v1")
    app = create_app(runner, token, port)
    app.router.lifespan_context = lifespan
    print(f"crewbots: {len(crew.bots)} bots ({', '.join(crew.bots)}) from {home}, on http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
