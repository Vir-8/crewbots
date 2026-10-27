"""The user's projects (adding, changing and removing them), and bringing in what Claude Code and Codex already know:
the folders the user works in, as projects, and their memories, for the chief to learn from."""

import asyncio
import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from crewbots import discovery, prompts
from crewbots import projects as projects_module
from crewbots.api.context import Context
from crewbots.api.schemas import BringIn, BroughtIn, FoundOut, ProjectBody, ProjectOut, ProjectsOut
from crewbots.projects import Project, ProjectError


def routes(c: Context) -> APIRouter:
    runner = c.runner
    v1 = APIRouter()

    def home() -> Path:
        return runner.crew.home

    def reload() -> None:
        """Takes up the projects as they are on disk now; the rest of the crew stays as it is."""
        loaded, errors = projects_module.load(home())
        runner.reload(dataclasses.replace(runner.crew, projects=loaded, project_errors=errors))

    @v1.get("/projects", response_model=ProjectsOut)
    async def projects():
        loaded, errors = projects_module.load(home())
        return {"projects": [_project_out(p) for p in loaded.values()], "errors": errors}

    @v1.put("/projects/{name}", response_model=ProjectOut)
    async def put_project(name: str, body: ProjectBody):
        try:
            project = projects_module.save(home(), name, Path(body.folder), body.about)
        except ProjectError as e:
            raise HTTPException(422, str(e)) from None
        reload()
        return _project_out(project)

    @v1.delete("/projects/{name}", status_code=204)
    async def delete_project(name: str):
        try:
            projects_module.delete(home(), name)
        except ProjectError as e:
            raise HTTPException(404, str(e)) from None
        reload()

    @v1.get("/projects/found", response_model=FoundOut)
    async def found():
        """The folders the user worked in lately with Claude Code or Codex, and the memories those keep."""
        folders, memories = await asyncio.to_thread(discovery.found)
        by_folder = {p.folder.resolve(): p.name for p in runner.crew.projects.values()}
        return {"folders": [_found_out(f, by_folder.get(f.folder)) for f in folders],
                "memories": [_memory_out(m) for m in memories]}

    @v1.post("/projects/bring-in", response_model=BroughtIn)
    async def bring_in(body: BringIn):
        """Adds the folders the user picked as projects, and hands the memories they picked to the chief, which keeps
        what's worth keeping and says what it learned. Only memories that were found can come in."""
        added: list[Project] = []
        for new in body.projects:
            try:
                added.append(projects_module.save(home(), new.name, Path(new.folder), new.about))
            except ProjectError as e:
                raise HTTPException(422, f"{new.name}: {e}") from None
        if added:
            reload()
        task_id = None
        picked = set(body.memories)
        if picked:
            folders, loose = await asyncio.to_thread(discovery.found)
            memories = [m for m in [*loose, *(m for f in folders for m in f.memories)] if str(m.path) in picked]
            if memories:
                chief = next((b for b in runner.crew.bots.values() if b.can_delegate), next(iter(runner.crew.bots.values())))
                text = prompts.text("bring-in", home(), memories=discovery.digest(memories))
                task_id = runner.send(runner.chat(chief.name).id, text, sender="crewbots").id
            picked = {str(m.path) for m in memories}
        return {"projects": [_project_out(p) for p in added], "memories": len(picked), "task_id": task_id}

    return v1


def _project_out(project: Project) -> dict[str, Any]:
    return {"name": project.name, "folder": _shown(project.folder), "about": project.about,
            "missing": not project.folder.is_dir()}


def _found_out(folder: discovery.FoundFolder, project: str | None) -> dict[str, Any]:
    return {"folder": str(folder.folder), "name": folder.name, "about": folder.about, "sources": sorted(folder.sources),
            "sessions": folder.sessions, "last_used": datetime.fromtimestamp(folder.last_used).astimezone().isoformat(),
            "memories": [_memory_out(m) for m in folder.memories], "project": project}


def _memory_out(memory: discovery.FoundMemory) -> dict[str, Any]:
    return {"path": str(memory.path), "source": memory.source, "title": memory.title}


def _shown(path: Path) -> str:
    return str(path).replace(str(Path.home()), "~", 1) if path.is_relative_to(Path.home()) else str(path)
