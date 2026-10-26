"""The crew's MCP servers (kept, checked, imported from other apps or the registry) and its skills."""

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

from crewbots import crew as crew_module
from crewbots import mcp_catalog
from crewbots.api.context import Context
from crewbots.api.schemas import (
    ImportBody,
    InstalledServer,
    McpCheck,
    McpServerBody,
    McpServerOut,
    RegistryAdd,
    RegistryEntry,
    SkillOut,
)


def routes(c: Context) -> APIRouter:
    runner = c.runner
    reload_crew = c.reload_crew
    v1 = APIRouter()

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

    return v1
