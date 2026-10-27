"""The user's projects (adding, changing and removing them), and bringing in what Claude Code and Codex already know:
the folders the user works in, as projects, and their memories, for the chief to learn from."""

import asyncio
import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from crewbots import discovery
from crewbots import projects as projects_module
from crewbots.api.context import Context
from crewbots.api.schemas import BringIn, BroughtIn, FoundOut, ProjectBody, ProjectOut, ProjectsOut
from crewbots.projects import Project, ProjectError


def routes(c: Context) -> APIRouter:
    runner, store = c.runner, c.store
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
        return {"projects": [_project_out(p) for p in loaded.values()], "errors": errors, "started": runner.started()}

    @v1.post("/projects/started", status_code=204)
    async def not_now():
        """The user would rather start without bringing anything in: the offer isn't made again."""
        runner.not_now()

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
        """The folders the user worked in lately with Claude Code or Codex, and the memories those keep: each with
        whether it's been brought in already, and whether it's changed since."""
        folders, memories = await asyncio.to_thread(discovery.found)
        by_folder = {p.folder.resolve(): p.name for p in runner.crew.projects.values()}
        now = await asyncio.to_thread(discovery.fingerprints, [*memories, *(m for f in folders for m in f.memories)])
        brought = store.brought_in()

        def memory_out(memory: discovery.FoundMemory) -> dict[str, Any]:
            was = brought.get(str(memory.path))
            return _memory_out(memory) | {"brought_at": was["at"] if was else None,
                                          "changed": bool(was) and was["fingerprint"] != now.get(memory.path)}

        return {"folders": [_found_out(f, by_folder.get(f.folder)) | {"memories": [memory_out(m) for m in f.memories]}
                            for f in folders],
                "memories": [memory_out(m) for m in memories]}

    @v1.post("/projects/bring-in", response_model=BroughtIn)
    async def bring_in(body: BringIn):
        """Adds the folders the user picked as projects, and has the chief learn from the memories they picked, in a
        thread of its own whose result comes back to its chat. Only memories that were found can come in."""
        picked = set(body.memories)
        memories: list[discovery.FoundMemory] = []
        if picked:
            folders, loose = await asyncio.to_thread(discovery.found)
            memories = [m for m in [*loose, *(m for f in folders for m in f.memories)] if str(m.path) in picked]
        try:
            added, thread = runner.bring_in([p.model_dump() for p in body.projects], memories)
        except ProjectError as e:
            raise HTTPException(422, str(e)) from None
        return {"projects": [_project_out(p) for p in added], "memories": len(memories),
                "task_id": thread.id if thread else None}

    return v1


def _project_out(project: Project) -> dict[str, Any]:
    return {"name": project.name, "folder": str(project.folder), "about": project.about,
            "missing": not project.folder.is_dir()}


def _found_out(folder: discovery.FoundFolder, project: str | None) -> dict[str, Any]:
    return {"folder": str(folder.folder), "name": folder.name, "about": folder.about, "sources": sorted(folder.sources),
            "sessions": folder.sessions, "last_used": datetime.fromtimestamp(folder.last_used).astimezone().isoformat(),
            "memories": [_memory_out(m) for m in folder.memories], "project": project}


def _memory_out(memory: discovery.FoundMemory) -> dict[str, Any]:
    return {"path": str(memory.path), "source": memory.source, "title": memory.title}
