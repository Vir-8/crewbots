"""The crew's MCP servers, and where to find more: the servers your other apps already have (Claude Code, Claude's
desktop app, Codex), and the official MCP registry.

The crew keeps its servers in `mcp.json`, in Claude's `{"mcpServers": {...}}` shape, so an entry can be copied from or
to any of those apps. A role card names the ones its bot may use (`mcp: [blender, gimp]`); nothing reaches a bot
otherwise. Values in `env` and `headers` can be secrets, so the file is the user's alone, and the API only ever
reports which keys a server has, never their values.
"""

import asyncio
import contextlib
import json
import os
import tomllib
from pathlib import Path
from typing import Any

import httpx

from crewbots import cards, paths
from crewbots.backends import McpServer
from crewbots.crew import MCP_FILE, MCP_NAME, CrewError, mcp_server

REGISTRY = "https://registry.modelcontextprotocol.io/v0"
CHECK_TIMEOUT_S = 30.0


# The crew's own servers.


def servers(home: Path) -> dict[str, dict[str, Any]]:
    """The crew's servers as written in mcp.json."""
    path = home / MCP_FILE
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text()).get("mcpServers") or {})


def save(home: Path, name: str, spec: dict[str, Any]) -> None:
    """Adds or replaces a server, after checking the crew could load it."""
    mcp_server(name, spec, MCP_FILE)
    _write(home, servers(home) | {name: spec})


def remove(home: Path, name: str) -> list[str]:
    """Removes a server, and takes it off every role card that names it. Returns those bots."""
    found = servers(home)
    if name not in found:
        raise CrewError(f"there's no MCP server called {name}")
    users = []
    for card_path in sorted((home / "bots").glob("*.md")):
        meta, _ = cards.read(card_path)
        if isinstance(meta.get("mcp"), list) and name in meta["mcp"]:
            cards.save_card(home, card_path.stem, {"mcp": [n for n in meta["mcp"] if n != name]}, None)
            users.append(card_path.stem)
    del found[name]
    _write(home, found)
    return users


def summary(name: str, spec: dict[str, Any], bots: list[str]) -> dict[str, Any]:
    """What the app shows of a server: never the values in its env or headers."""
    server = mcp_server(name, spec, MCP_FILE)
    return {"name": name, "transport": server.transport, "command": server.command or None, "args": server.args,
            "url": server.url, "env_keys": sorted(server.env), "header_keys": sorted(server.headers), "bots": bots}


def _write(home: Path, found: dict[str, Any]) -> None:
    path = home / MCP_FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)  # its env and headers may hold keys
    with os.fdopen(fd, "w") as f:
        json.dump({"mcpServers": found}, f, indent=2)
        f.write("\n")


# The servers your other apps have.


def installed() -> list[dict[str, Any]]:
    """Every server in Claude Code's, Claude's desktop app's and Codex's configs, each once per source, in Claude's
    shape, with where it came from. Servers switched off in Codex are left out."""
    found: list[dict[str, Any]] = []

    def add(source: str, name: str, spec: Any) -> None:
        if name == "crewbots" or not isinstance(spec, dict) or not MCP_NAME.fullmatch(name):
            return
        if any(f["name"] == name and f["spec"] == spec for f in found):
            return  # the same server in several Unity projects, say
        found.append({"id": f"{source}:{name}:{len(found)}", "source": source, "name": name, "spec": spec})

    with contextlib.suppress(OSError, ValueError):
        config = json.loads(paths.claude_code_config().read_text())
        for name, spec in (config.get("mcpServers") or {}).items():
            add("Claude Code", name, spec)
        for project, settings in (config.get("projects") or {}).items():
            for name, spec in (settings.get("mcpServers") or {}).items():
                add(f"Claude Code · {Path(project).name}", name, spec)
    with contextlib.suppress(OSError, ValueError):
        for name, spec in (json.loads(paths.claude_desktop_config().read_text()).get("mcpServers") or {}).items():
            add("Claude desktop", name, spec)
    with contextlib.suppress(OSError, ValueError, tomllib.TOMLDecodeError):
        for name, raw in (tomllib.loads(paths.codex_config().read_text()).get("mcp_servers") or {}).items():
            if raw.get("enabled", True) is not False:
                add("Codex", name, _from_codex(raw))
    return [f for f in found if _loads(f["name"], f["spec"])]


def _from_codex(raw: dict[str, Any]) -> dict[str, Any]:
    if raw.get("url"):
        headers = dict(raw.get("http_headers") or {})
        if raw.get("bearer_token_env_var") and os.environ.get(raw["bearer_token_env_var"]):
            headers["Authorization"] = f"Bearer {os.environ[raw['bearer_token_env_var']]}"
        return {"type": "http", "url": raw["url"], **({"headers": headers} if headers else {})}
    return {"command": raw.get("command", ""), "args": list(raw.get("args") or []), "env": dict(raw.get("env") or {})}


def _loads(name: str, spec: dict[str, Any]) -> bool:
    try:
        mcp_server(name, spec, "")
        return True
    except CrewError:
        return False


# The official registry.


async def search(query: str, limit: int = 30) -> list[dict[str, Any]]:
    """Servers in the MCP registry whose name has `query` in it, latest versions only, each with the ways it can run
    (a package to start, or an address it's already at) and what each needs filled in."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{REGISTRY}/servers", params={"search": query, "limit": limit, "version": "latest"})
        response.raise_for_status()
    return [_entry(item.get("server") or {}) for item in response.json().get("servers") or []]


async def fetch(name: str, version: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{REGISTRY}/servers/{name.replace('/', '%2F')}/versions/{version}")
        response.raise_for_status()
    return _entry(response.json().get("server") or {})


def _entry(server: dict[str, Any]) -> dict[str, Any]:
    options = []
    for i, package in enumerate(server.get("packages") or []):
        if (package.get("transport") or {}).get("type", "stdio") != "stdio":
            continue  # a package that serves on a port it opens itself: not one a bot's CLI can start
        fields = [*(_field(v, "env") for v in package.get("environmentVariables") or []),
                  *(_field(a, "arg") for a in package.get("packageArguments") or [] if a.get("isRequired") or a.get("name"))]
        options.append({"id": f"package:{i}", "label": f"{package.get('registryType', 'package')} · "
                        f"{package.get('identifier', '')} {package.get('version', '')}".strip(), "fields": fields})
    for i, remote in enumerate(server.get("remotes") or []):
        options.append({"id": f"remote:{i}", "label": f"Remote · {remote.get('url', '')}",
                        "fields": [_field(h, "header") for h in remote.get("headers") or []]})
    return {"name": server.get("name", ""), "title": server.get("title") or None,
            "description": server.get("description", ""), "version": server.get("version", ""),
            "website": server.get("websiteUrl") or (server.get("repository") or {}).get("url"),
            "options": options, "raw": server}


def _field(raw: dict[str, Any], kind: str) -> dict[str, Any]:
    return {"kind": kind, "key": raw.get("name") or raw.get("valueHint") or "", "description": raw.get("description") or "",
            "required": bool(raw.get("isRequired")), "secret": bool(raw.get("isSecret")),
            "default": raw.get("default") or raw.get("value") or None}


def from_registry(entry: dict[str, Any], option: str, values: dict[str, str]) -> dict[str, Any]:
    """The mcp.json entry for one of a registry server's ways to run, with what the user filled in."""
    kind, _, index = option.partition(":")
    server = entry["raw"]
    chosen = next((o for o in entry["options"] if o["id"] == option), None)
    if chosen is None:
        raise CrewError(f"{entry['name']} has no option {option}")
    missing = [f["key"] for f in chosen["fields"] if f["required"] and not (values.get(f["key"]) or f["default"])]
    if missing:
        raise CrewError(f"{entry['name']} needs {', '.join(missing)}")

    def value(f: dict[str, Any]) -> str:
        return values.get(f["key"]) or f["default"] or ""

    if kind == "remote":
        remote = server["remotes"][int(index)]
        headers = {f["key"]: value(f) for f in chosen["fields"] if value(f)}
        return {"type": "sse" if remote.get("type") == "sse" else "http", "url": remote["url"],
                **({"headers": headers} if headers else {})}
    package = server["packages"][int(index)]
    ident, version = package.get("identifier", ""), package.get("version", "")
    runtime = [a.get("value") for a in package.get("runtimeArguments") or [] if a.get("value")]
    match package.get("registryType"):
        case "npm":
            command, args = "npx", [*(runtime or ["-y"]), f"{ident}@{version}" if version else ident]
        case "pypi":
            command, args = "uvx", [*runtime, f"{ident}=={version}" if version else ident]
        case "oci":
            command, args = "docker", ["run", "-i", "--rm", *runtime,
                                       *(a for f in chosen["fields"] if f["kind"] == "env" for a in ("-e", f["key"])),
                                       f"{ident}:{version}" if version and ":" not in ident else ident]
        case other:
            raise CrewError(f"crewbots can't start a {other} package yet; add it by hand instead")
    for f in chosen["fields"]:
        if f["kind"] == "arg" and value(f):
            args += [f"--{f['key']}", value(f)] if f["key"] and not f["key"].startswith("-") else [value(f)]
    env = {f["key"]: value(f) for f in chosen["fields"] if f["kind"] == "env" and value(f)}
    return {"command": command, "args": args, **({"env": env} if env else {})}


# Checking one works.


async def check(server: McpServer) -> dict[str, Any]:
    """Starts or reaches the server and lists its tools: whether a bot given it will have anything to use."""
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.stdio import StdioServerParameters, stdio_client
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    async def tools() -> list[str]:
        if server.transport == "stdio":
            params = StdioServerParameters(command=server.command, args=server.args, env={**os.environ, **server.env})
            with open(os.devnull, "w") as quiet:
                async with stdio_client(params, errlog=quiet) as (read, write), ClientSession(read, write) as session:
                    await session.initialize()
                    return [t.name for t in (await session.list_tools()).tools]
        if server.transport == "sse":
            async with sse_client(server.url, headers=server.headers) as (read, write), ClientSession(read, write) as s:
                await s.initialize()
                return [t.name for t in (await s.list_tools()).tools]
        async with create_mcp_http_client(headers=server.headers) as http, \
                streamable_http_client(server.url, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                return [t.name for t in (await session.list_tools()).tools]

    try:
        names = await asyncio.wait_for(tools(), CHECK_TIMEOUT_S)
    except TimeoutError:
        return {"ok": False, "tools": [], "error": f"no answer in {CHECK_TIMEOUT_S:g}s"}
    except Exception as e:  # it wouldn't start, refused the connection, or broke the protocol
        return {"ok": False, "tools": [], "error": _reason(e)}
    return {"ok": True, "tools": sorted(names), "error": None}


def _reason(e: BaseException) -> str:
    """The first real cause inside the exception groups anyio wraps failures in."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return str(e) or type(e).__name__
