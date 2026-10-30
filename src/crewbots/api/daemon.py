"""Running the daemon: the HTTP server, its lifespan (resuming cut-off turns, the scheduler, warming the CLIs and
GitHub, rotating the log), and daemon.json, where clients find it."""

import asyncio
import contextlib
import json
import os
import secrets
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from crewbots import crew as crew_module
from crewbots import plans as plans_module
from crewbots import service as service_module
from crewbots.api.app import DEFAULT_PORT, create_app
from crewbots.backends import codex_isolation
from crewbots.paths import DAEMON_FILE, DB
from crewbots.reviews import Reviews
from crewbots.runner import Runner
from crewbots.scheduler import Scheduler
from crewbots.store import Store

LOG_CHECK_S = 600.0  # how often the daemon looks at its log's size


def _warm_inbox(reviews: Reviews) -> None:
    with contextlib.suppress(Exception):  # the app asks again, and shows GitHub's error then
        reviews.inbox()


async def _rotate_log_now_and_then() -> None:
    while True:
        if service_module.rotate_log():
            print("started a fresh daemon.log; the last one is daemon.log.1", flush=True)
        await asyncio.sleep(LOG_CHECK_S)



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


def serve(home: Path, port: int = DEFAULT_PORT, max_parallel: int = 4, sessions: bool = True, max_depth: int = 3) -> None:
    home = home.expanduser()
    crew = crew_module.load(home)
    store = Store(home / DB)
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
        runner.make_standing_rooms()
        if resumed := runner.resume_interrupted():
            print(f"carrying on {len(resumed)} turn(s) the last stop cut off", flush=True)
        runner.capacity.source = plans_module.plans  # background work goes by the plans' limits
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
                    keep_awake=keep_awake, max_depth=max_depth)
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
