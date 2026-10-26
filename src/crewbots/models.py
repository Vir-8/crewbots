"""Which models each CLI offers, and the reasoning efforts each takes, asked of the CLIs themselves.

The lists depend on the CLI's version and your plan, so nothing is hard-coded: Codex answers `model/list` on its
app-server, and Claude Code reports its models when a session starts. Asking takes a few seconds, so the answer is
kept for a while.
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import time
from typing import Any

TTL_S = 30 * 60
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


async def models(fresh: bool = False) -> dict[str, list[dict[str, Any]]]:
    """{claude: [...], codex: [...]}, each model as {id, label, description, efforts, default_effort, default}."""
    async def one(name: str, ask) -> list[dict[str, Any]]:
        hit = _cache.get(name)
        if hit and not fresh and time.monotonic() - hit[0] < TTL_S:
            return hit[1]
        try:
            found = await asyncio.wait_for(ask(), 60)
        except Exception:
            return hit[1] if hit else []  # a CLI that isn't there, or didn't answer, offers nothing for now
        _cache[name] = (time.monotonic(), found)
        return found

    claude, codex = await asyncio.gather(one("claude", _claude), one("codex", _codex))
    return {"claude": claude, "codex": codex}


async def _codex() -> list[dict[str, Any]]:
    process = await asyncio.create_subprocess_exec(
        "codex", "app-server", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024,  # the model list is one long line
        start_new_session=True)  # its own process group: the helpers it starts hold its pipes open
    assert process.stdin and process.stdout

    async def ask(request_id: int, method: str, params: Any) -> dict[str, Any]:
        process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                             + "\n").encode())
        await process.stdin.drain()
        while line := await process.stdout.readline():
            message = json.loads(line)
            if message.get("id") == request_id:
                return message.get("result") or {}
        return {}

    try:
        await ask(1, "initialize", {"clientInfo": {"name": "crewbots", "version": "0"}, "capabilities": None})
        process.stdin.write(b'{"jsonrpc": "2.0", "method": "initialized"}\n')
        listed = await ask(2, "model/list", {})
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
    return [{
        "id": m["id"], "label": m.get("displayName") or m["id"], "description": m.get("description") or "",
        "efforts": [e.get("reasoningEffort", e) if isinstance(e, dict) else e for e in m.get("supportedReasoningEfforts") or []],
        "default_effort": m.get("defaultReasoningEffort"), "default": bool(m.get("isDefault")),
    } for m in listed.get("data") or [] if not m.get("hidden")]


async def _claude() -> list[dict[str, Any]]:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    env = {"ANTHROPIC_API_KEY": ""} if "ANTHROPIC_API_KEY" in os.environ else {}
    options = ClaudeAgentOptions(cli_path=shutil.which("claude"), setting_sources=[], env=env, tools=[])
    async with ClaudeSDKClient(options) as client:
        info = await client.get_server_info() or {}
    found = []
    for m in info.get("models") or []:
        value = m.get("value")
        if not value:
            continue
        # The CLI names models by family ("Sonnet") and puts the version in the description ("Sonnet 5.5 · ...").
        description = m.get("description") or ""
        versioned = description.split(" · ")[0].strip()
        label = versioned if re.fullmatch(r"[A-Z][\w ]* \d+(\.\d+)*", versioned) else m.get("displayName") or value
        if value == "default":
            label = f"Default ({versioned})" if versioned else "Default"
        found.append({"id": value, "label": label, "description": description,
                      "efforts": list(m.get("supportedEffortLevels") or []), "default_effort": None,
                      "default": value == "default"})  # the CLI's own pick, the same as leaving the model blank
    return found
