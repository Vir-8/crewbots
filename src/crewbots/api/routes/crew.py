"""The crew as a whole: pausing, restarting, reloading, the daemon's version and updating crewbots, the app's errors, and
the agent log."""

import asyncio
import sys
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Query

from crewbots import crew as crew_module
from crewbots import typescript
from crewbots.api.context import API_VERSION, PROJECT, Context, checkout_commit, started_from
from crewbots.api.schemas import (
    AgentLogOut,
    ClientError,
    Consolidate,
    CrewEventOut,
    CrewState,
    DigestOut,
    IntentOut,
    MergeSettings,
    ProactiveOut,
    Reaction,
    RestartBody,
    TaskOut,
    UpdateBody,
    UpdateOut,
    VersionOut,
)
from crewbots.updates import UpdateError, Updates

RESTART_EXIT = 75  # EX_TEMPFAIL: stopped to be started again
CLIENT_ERRORS_PER_MINUTE = 30


def routes(c: Context) -> APIRouter:
    runner, app = c.runner, c.app
    v1 = APIRouter()

    @v1.get("/crew", response_model=CrewState)
    async def crew_state():
        return runner.state()

    @v1.get("/crew/merge-settings", response_model=MergeSettings)
    async def merge_settings():
        return {"auto_merge": c.store.auto_merge()}

    @v1.put("/crew/merge-settings", response_model=MergeSettings)
    async def set_merge_settings(body: MergeSettings):
        c.store.set_auto_merge(body.auto_merge)
        return {"auto_merge": c.store.auto_merge()}

    @v1.get("/crew/chief")
    async def chief_setting():
        return {"chief": c.store.chief(), "available": runner.coordinator() is not None}

    @v1.put("/crew/chief")
    async def set_chief(body: dict[str, str]):
        name = body.get("chief", "")
        if name not in runner.crew.bots:
            raise HTTPException(422, f"no bot named {name!r}")
        await runner.set_coordinator(name)
        return {"chief": name, "available": True}

    @v1.get("/crew/coordinators")
    async def coordinator_settings():
        return {"coordinators": c.store.coordinators() or []}

    @v1.put("/crew/coordinators")
    async def set_coordinators(body: dict[str, list[str]]):
        names = body.get("coordinators")
        if names is None or len(names) != len(set(names)) or any(name not in runner.crew.bots for name in names):
            raise HTTPException(422, "coordinators must be distinct existing bot handles")
        await runner.set_coordinators(names)
        return {"coordinators": names}

    @v1.post("/crew/pause", response_model=CrewState)
    async def pause():
        """No new turn starts until resumed; turns already running finish."""
        runner.pause()
        return runner.state()

    @v1.post("/crew/resume", response_model=CrewState)
    async def resume():
        runner.resume()
        return runner.state()

    build = started_from()
    updates = Updates(PROJECT, build)

    @v1.get("/update", response_model=UpdateOut)
    async def check_update(app: str | None = None, fetch: bool = False):
        """What updating crewbots from its checkout would bring (`fetch`: asking GitHub too), and how the last update
        went."""
        return await asyncio.to_thread(updates.check, app, fetch)

    @v1.post("/update", response_model=UpdateOut, status_code=202)
    async def start_update(body: UpdateBody):
        """Updates crewbots: scripts/release, apart from the daemon, which it restarts once the crew is idle. The app
        restarts itself into the new build once it's done."""
        try:
            return await asyncio.to_thread(updates.start, body.app)
        except UpdateError as e:
            raise HTTPException(409, str(e)) from None

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

    @v1.post("/memory/consolidate", status_code=201, response_model=TaskOut)
    async def consolidate(body: Consolidate):
        """Consolidates a bot's memory now, rather than overnight."""
        return runner.dream(body.bot, body.weekly).to_json()

    @v1.post("/proactive/{item_id}/react", response_model=ProactiveOut)
    async def react(item_id: int, body: Reaction):
        """The user's 👍 or 👎 on something the crew raised on its own: a note of its bot's, about what's worth it."""
        try:
            return runner.attention.react(item_id, body.reaction)
        except KeyError:
            raise HTTPException(404, f"nothing raised with id {item_id}") from None

    @v1.post("/digest", response_model=DigestOut | None)
    async def digest():
        """The morning digest, now: the last day's FYIs not yet in one, in the chief's chat."""
        return runner.attention.digest(runner.attention.now())

    @v1.get("/crew-events", response_model=list[CrewEventOut])
    async def crew_events(since: str = "", limit: int = Query(100, ge=1, le=1000)):
        """What happened around the crew (`sources.py`), oldest first."""
        return c.store.crew_events(since, limit)

    @v1.get("/intents", response_model=list[IntentOut])
    async def intents():
        """Standing intents still waiting for their event."""
        return c.store.intents()

    @v1.delete("/intents/{intent_id}", response_model=IntentOut)
    async def cancel_intent(intent_id: int):
        try:
            return c.store.cancel_intent(intent_id)
        except KeyError:
            raise HTTPException(404, f"no standing intent {intent_id}") from None

    @v1.get("/agent-log", response_model=list[AgentLogOut])
    async def agent_log(limit: int = Query(200, ge=1, le=5000), before: int | None = None):
        """What bots did to each other, newest first: every delegation, review asked for, question, message and
        group chat post."""
        return c.store.agent_log(limit, before)

    @v1.get("/version", response_model=VersionOut)
    async def version():
        now = await asyncio.to_thread(checkout_commit)
        return {"api": API_VERSION, "schema_hash": typescript.schema_hash(app.openapi()), "build": build,
                "source_changed": bool(build and now and now != build)}

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
