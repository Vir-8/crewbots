"""The daemon's API: HTTP for commands, one WebSocket for live updates. The desktop app and `crewbots` both use it.

It listens on localhost only, and every request needs a token. A browser tab can reach localhost too, so the Host
header is checked as well (a DNS-rebinding page arrives with its own host name).

Every route is under /v1. There are two kinds of token. The daemon's token, in daemon.json, is the user's and opens
everything but /v1/bot/. A run token is minted for one run of one task and opens only /v1/bot/, acting as that task:
that is what a bot's crewbots MCP server holds, so a bot can never act for another task, or for the user.
"""

import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from crewbots import crew as crew_module
from crewbots.api.context import API_VERSION, Context
from crewbots.api.routes import attachments, bot, bots, chats, crew, events, mcp, reviews, routines, tasks, usage
from crewbots.memory import MemoryError
from crewbots.reviews import Reviews, ReviewTools
from crewbots.routines import RoutineError
from crewbots.runner import MergeError, NotAllowed, Runner, UnknownBot
from crewbots.scheduler import Scheduler
from crewbots.store import Store, Task

DEFAULT_PORT = 8765
AREAS = (bots, mcp, tasks, attachments, chats, reviews, usage, crew, routines, events, bot)


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
        if task := getattr(request.state, "task", None):  # a bot's tool call refused: logged with what bots do
            store.log_agent("refused", task.bot, task_id=task.id, text=f"{request.url.path.removeprefix('/v1/bot/')}: {e}")
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

    c = Context(runner=runner, store=store, scheduler=scheduler, reviews=reviews, app=app, allowed=allowed)
    v1 = APIRouter(prefix="/v1")
    for area in AREAS:
        v1.include_router(area.routes(c))
    app.include_router(v1)
    return app


def openapi_schema() -> dict[str, Any]:
    """The API's schema, without a crew or a database: what app/src/api-types.ts is generated from."""
    from crewbots.crew import Crew

    runner = Runner(Store(":memory:"), Crew(home=Path("."), bots={}, team_files={}))
    return create_app(runner, token="schema").openapi()
