"""The crew as a whole: pausing, restarting, reloading, the daemon's version, and the app's errors."""

import asyncio
import sys
import time
from collections import deque

from fastapi import APIRouter

from crewbots import crew as crew_module
from crewbots import typescript
from crewbots.api.context import API_VERSION, Context, checkout_commit
from crewbots.api.schemas import ClientError, CrewState, RestartBody, VersionOut

RESTART_EXIT = 75  # EX_TEMPFAIL: stopped to be started again
CLIENT_ERRORS_PER_MINUTE = 30


def routes(c: Context) -> APIRouter:
    runner, app = c.runner, c.app
    v1 = APIRouter()

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

    started_from = checkout_commit()

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
        now = await asyncio.to_thread(checkout_commit)
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

    return v1
