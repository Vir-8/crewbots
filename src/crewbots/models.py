"""Which models each CLI offers, and the reasoning efforts each takes, asked of the CLIs themselves.

The lists depend on the CLI's version and your plan, so nothing is hard-coded: Codex answers `model/list` on its
app-server, and Claude Code reports its models when a session starts. Asking takes a few seconds, so the answer is
kept for a while.
"""

import asyncio
import os
import re
import shutil
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

    from crewbots.backends.antigravity_runtime import models as antigravity_models

    claude, codex, antigravity = await asyncio.gather(
        one("claude", _claude), one("codex", _codex), one("antigravity", antigravity_models))
    return {"claude": claude, "codex": codex, "antigravity": antigravity}


def family(backend: str) -> str:
    """Which CLI's list a backend's models come from: claude-cli runs Claude's, codex-exec Codex's."""
    return "antigravity" if backend == "antigravity" else "codex" if backend.startswith("codex") else "claude"


def efforts(listed: dict[str, list[dict[str, Any]]], backend: str, model: str | None) -> list[str] | None:
    """The efforts a model takes, from its CLI's list (blank: the CLI's default model); None when the CLI listed
    nothing, so it can't be told. A model the CLI doesn't list takes none."""
    found = listed.get(family(backend)) or []
    if not found:
        return None
    chosen = next((m for m in found if (m["id"] == model if model else m["default"])), None)
    return chosen["efforts"] if chosen else []


async def _codex() -> list[dict[str, Any]]:
    from crewbots.backends.codex_app_server import ask_once

    listed = await ask_once("model/list", {})
    return [{
        "id": m["id"], "label": m.get("displayName") or m["id"], "description": m.get("description") or "",
        "efforts": [e.get("reasoningEffort", e) if isinstance(e, dict) else e for e in m.get("supportedReasoningEfforts") or []],
        "default_effort": m.get("defaultReasoningEffort"), "default": bool(m.get("isDefault")),
    } for m in listed.get("data") or [] if not m.get("hidden")]


async def _claude() -> list[dict[str, Any]]:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

    from crewbots.backends.base import CLAUDE_BOT_ENV

    env = CLAUDE_BOT_ENV | ({"ANTHROPIC_API_KEY": ""} if "ANTHROPIC_API_KEY" in os.environ else {})
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
