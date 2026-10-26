"""Keeps a crew bot on Codex from inheriting the user's own MCP servers, plugins and apps.

Codex merges `-c` overrides into the user's config.toml, so an override can add a server but never remove one, and
`--strict`-style isolation (what Claude has) doesn't exist. Instead every server Codex would start is switched off by
name: servers from config.toml get `enabled=false`; servers that come from elsewhere (bundled plugins, the ChatGPT
app's computer use) get a disabled stub, since a bare `enabled=false` on them is an invalid config. Plugins in
config.toml and the apps feature are switched off too. The role card's own servers are then added on top.

Without this, a bot could reach whatever the user connected Codex to: their trading account, Slack, their screen.
"""

import json
import os
import re
import subprocess
import time
import tomllib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

CACHE_S = 3600.0  # `codex mcp list` takes about 5s, and its answer only changes with the config or the plugins
LIST_TIMEOUT_S = 30.0
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")

_cache: dict[tuple[str, ...], tuple[float, tuple[float, float], list[str]]] = {}


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def isolation_overrides(command: Sequence[str] = ("codex",), keep: Iterable[str] = ()) -> list[str]:
    """`-c` arguments that switch off everything the user's Codex would start, except the servers named in `keep`."""
    config = _config()
    servers, error = _listed_servers(command)
    keep = set(keep)
    args: list[str] = []
    for name in sorted(set(servers) | set(config.get("mcp_servers", {})) - keep):
        key = f"mcp_servers.{_key(name)}"
        if name in config.get("mcp_servers", {}):
            args += ["-c", f"{key}.enabled=false"]
        else:
            args += ["-c", f'{key}={{command="false", enabled=false}}']
    for plugin in sorted(config.get("plugins", {})):
        args += ["-c", f"plugins.{_key(plugin)}.enabled=false"]
    args += ["-c", "features.apps=false"]
    if error:
        # Without the list, servers that aren't in config.toml can't be switched off. Better not to run at all.
        raise RuntimeError(f"couldn't list Codex's MCP servers to isolate the bot from them: {error}")
    return args


def _config() -> dict[str, Any]:
    path = codex_home() / "config.toml"
    try:
        return tomllib.loads(path.read_text())
    except FileNotFoundError:
        return {}


def warm(command: Sequence[str] = ("codex",)) -> None:
    """Fills the cache ahead of the first Codex run (the daemon does this at start-up, off its event loop)."""
    _listed_servers(command)


def _listed_servers(command: Sequence[str]) -> tuple[list[str], str | None]:
    """Every server Codex knows about, from `codex mcp list --json`, cached while the config and plugins are unchanged."""
    stamp = (_mtime(codex_home() / "config.toml"), _mtime(codex_home() / "plugins"))
    key = tuple(command)
    cached = _cache.get(key)
    if cached and cached[1] == stamp and time.monotonic() - cached[0] < CACHE_S:
        return cached[2], None
    try:
        result = subprocess.run([*command, "mcp", "list", "--json"], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=LIST_TIMEOUT_S)
        names = [s["name"] for s in json.loads(result.stdout or "[]")]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as e:
        return [], str(e)
    _cache[key] = (time.monotonic(), stamp, names)
    return names, None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _key(name: str) -> str:
    return name if BARE_KEY.fullmatch(name) else json.dumps(name)
