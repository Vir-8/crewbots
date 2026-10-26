"""Routines: listing, editing, adding and running them; and checking a bot in now."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from crewbots import cards
from crewbots import routines as routines_module
from crewbots.api.context import Context
from crewbots.api.schemas import NewRoutine, RoutineBody, RoutineFileOut, RoutineOut, RoutinesOut, TaskOut


def routes(c: Context) -> APIRouter:
    runner, scheduler = c.runner, c.scheduler
    v1 = APIRouter()

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

    @v1.post("/checkins/{bot}", status_code=201, response_model=TaskOut)
    async def check_in(bot: str):
        """Checks a bot in on its whole checklist now, whatever's due."""
        if bot not in runner.crew.bots:
            raise HTTPException(404, f"no bot named {bot}")
        try:
            return scheduler.check_in(bot).to_json()
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

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

    @v1.post("/routines/{name}/confirm", response_model=TaskOut | None)
    async def confirm_routine(name: str):
        """Turns on a routine a bot proposed, and runs it once now, as a test you watch (a trigger's waits for one)."""
        try:
            cards.save_routine(runner.crew.home, name, {"proposed_by": None, "enabled": True}, None)
        except KeyError:
            raise HTTPException(404, f"no routine named {name}") from None
        scheduler.load()
        routine = next(r for r in scheduler.routines if r.name == name)
        return scheduler.run_now(name).to_json() if routine.schedule else None

    @v1.post("/routines/{name}/run", status_code=201, response_model=TaskOut)
    async def run_routine(name: str):
        """Runs a scheduled routine now, as if its time had come."""
        return scheduler.run_now(name).to_json()

    return v1


def _routine_out(view: Any) -> dict[str, Any]:
    r = view.routine
    return {"name": r.name, "description": r.description, "bot": r.bot, "when": r.when,
            "schedule": r.schedule.text if r.schedule else None, "on": r.on, "repo": r.repo,
            "workspace": str(r.workspace) if r.workspace else None, "budget": r.budget, "enabled": r.enabled,
            "path": str(r.path), "last_run_at": view.last_run_at, "last_task_id": view.last_task_id,
            "next_run_at": view.next_run_at, "skipped_at": view.skipped_at, "proposed_by": r.proposed_by}
