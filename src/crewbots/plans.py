"""Where your Claude and Codex plans stand: how much of each limit is used, and when it resets.

Asked of the CLIs themselves, without running a model: Claude Code's `/usage` (a local command that prints the
current session's and week's limits), and Codex's app-server (`account/rateLimits/read`). Each CLI takes a few
seconds to start, so answers are kept: the daemon asks once when it starts, an answer older than a couple of minutes
is still served while a fresh one is asked for behind it, and only one ask per CLI runs at a time.
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import tempfile
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

TTL_S = 120.0
USAGE_LINE = re.compile(r"^Current ([^:]+):\s*(\d+(?:\.\d+)?)% used(?:\s*·\s*resets (.+?)(?:\s*\(([^)]+)\))?)?\s*$")
_cache: dict[str, dict[str, Any]] = {}  # each plan's last good answer, with `checked_at` (unix time)
_asking: dict[str, asyncio.Task] = {}


async def plans(fresh: bool = False) -> dict[str, Any]:
    """Each plan's limits. Kept answers come back at once, and one older than `TTL_S` is refreshed behind them;
    `fresh` waits for a new answer instead. A plan never answered yet is waited for."""
    async def one(name: str) -> dict[str, Any]:
        kept = _cache.get(name)
        if kept and not fresh:
            if time.time() - kept["checked_at"] >= TTL_S:
                _ask(name)
            return kept
        return await asyncio.shield(_ask(name))

    claude, codex = await asyncio.gather(one("claude"), one("codex"))
    return {"claude": claude, "codex": codex}


def _ask(name: str) -> asyncio.Task:
    """The ask under way for `name`, or a new one. A failed ask leaves the last good answer in place."""
    if name in _asking:
        return _asking[name]

    async def ask() -> dict[str, Any]:
        try:
            found = await asyncio.wait_for((_claude if name == "claude" else _codex)(), 60)
        except Exception as e:
            return _cache.get(name) or {"available": False, "error": str(e) or type(e).__name__, "plan": None,
                                        "windows": [], "checked_at": time.time()}
        finally:
            _asking.pop(name, None)
        _cache[name] = found | {"checked_at": time.time()}
        return _cache[name]

    _asking[name] = asyncio.create_task(ask(), name=f"plans:{name}")
    return _asking[name]


async def _claude() -> dict[str, Any]:
    cli = shutil.which("claude")
    if not cli:
        raise RuntimeError("Claude Code isn't installed")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}  # the subscription's limits, not an API key's
    # Without MCP servers, Chrome or a saved session, and away from any project's settings, it starts in about two
    # thirds of the time. `--bare` would be quicker still, but it never reads the sign-in, so it has no limits to show.
    process = await asyncio.create_subprocess_exec(
        cli, "-p", "/usage", "--strict-mcp-config", "--no-chrome", "--no-session-persistence",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, stdin=asyncio.subprocess.DEVNULL, env=env,
        cwd=tempfile.gettempdir())
    out, _ = await process.communicate()
    return {"available": True, "error": None, "plan": "subscription", "windows": parse_claude_usage(out.decode())}


def parse_claude_usage(text: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """`Current session: 5% used · resets Oct 26 at 5:40pm (Asia/Calcutta)` → {name: "Session", used_percent: 5, ...}."""
    windows = []
    for line in text.splitlines():
        match = USAGE_LINE.match(line.strip())
        if not match:
            continue
        name, used, resets, zone = match.groups()
        windows.append({"name": _window_name(name), "used_percent": float(used),
                        "resets_at": _claude_reset(resets, zone, now) if resets else None})
    return windows


def _window_name(name: str) -> str:
    if name.strip().lower() == "session":
        return "5-hour session"
    week = re.match(r"week(?:\s*\((.+)\))?", name.strip(), re.IGNORECASE)
    if week:
        return f"Weekly · {week.group(1)}" if week.group(1) and week.group(1).lower() != "all models" else "Weekly"
    return name.strip().capitalize()


def _claude_reset(text: str, zone: str | None, now: datetime | None) -> int | None:
    tz = ZoneInfo(zone) if zone else None
    now = now or datetime.now(tz)
    for fmt in ("%b %d at %I:%M%p", "%b %d at %I%p", "%I:%M%p", "%I%p"):
        try:
            parsed = datetime.strptime(text.strip().replace(" ", " "), fmt)
        except ValueError:
            continue
        if "%b" in fmt:
            when = parsed.replace(year=now.year, tzinfo=tz)
            if when.timestamp() < now.timestamp() - 86_400:
                when = when.replace(year=now.year + 1)  # "Jan 2" seen in late December
        else:
            when = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            if when < now:
                when += timedelta(days=1)  # "resets 3am", said at 11pm
        return int(when.timestamp())
    return None


async def _codex() -> dict[str, Any]:
    cli = shutil.which("codex")
    if not cli:
        raise RuntimeError("Codex isn't installed")
    process = await asyncio.create_subprocess_exec(
        cli, "app-server", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=16 * 1024 * 1024, start_new_session=True)
    assert process.stdin and process.stdout

    async def ask(request_id: int, method: str, params: Any) -> dict[str, Any]:
        process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
                             + "\n").encode())
        await process.stdin.drain()
        while line := await process.stdout.readline():
            message = json.loads(line)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(message["error"].get("message", "error"))
                return message.get("result") or {}
        return {}

    try:
        await ask(1, "initialize", {"clientInfo": {"name": "crewbots", "version": "0"},
                                    "capabilities": {"experimentalApi": True, "requestAttestation": False}})
        process.stdin.write(b'{"jsonrpc": "2.0", "method": "initialized"}\n')
        limits = await ask(2, "account/rateLimits/read", None)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
    snapshot = limits.get("rateLimits") or {}
    windows = []
    for key in ("primary", "secondary"):
        w = snapshot.get(key)
        if w and w.get("usedPercent") is not None:
            minutes = w.get("windowDurationMins") or 0
            name = (f"{minutes // 60}-hour" if minutes < 1440 else "Weekly" if minutes == 10080 else f"{minutes // 1440}-day")
            windows.append({"name": name, "used_percent": float(w["usedPercent"]), "resets_at": w.get("resetsAt")})
    return {"available": True, "error": None, "plan": snapshot.get("planType"), "windows": windows}
