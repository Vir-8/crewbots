"""Unity Editors crewbots opens and closes for work in a worktree of its own: Unity opens a folder in one Editor only,
so the one the user has open is on the project, not the worktree. An Editor is started straight from the version the
project names, as Unity Hub would, and connects to the Unity MCP by itself once MCP for Unity's "Auto-Start on Editor
Load" is on, an Editor preference that opening one turns on. Checked with Unity 6000.3 and MCP for Unity 10.1: a fresh
worktree of a small project opened and connected in 68 seconds, again in 10, and closed in 1 on SIGTERM."""

import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path

import httpx

HUB_EDITORS = Path("/Applications/Unity/Hub/Editor")
HUB_OTHER = Path.home() / "Library" / "Application Support" / "UnityHub" / "secondaryInstallPath.json"
PREFS, AUTO_START = "com.unity3d.UnityEditor5.x", "MCPForUnity.AutoStartOnLoad"
EDITOR = "Unity.app/Contents/MacOS/Unity"
PROJECT = re.compile(r"-projectPath (.+?)(?= -[A-Za-z]|$)", re.IGNORECASE)


def version(folder: Path) -> str | None:
    try:
        found = re.search(r"^m_EditorVersion:\s*(\S+)", (folder / "ProjectSettings" / "ProjectVersion.txt").read_text(),
                          re.MULTILINE)
    except OSError:
        return None
    return found[1] if found else None


def editor_app(folder: Path) -> Path | None:
    """The installed Unity.app of the version the project names, where Unity Hub keeps it or in its other folder."""
    if not (wanted := version(folder)):
        return None
    places = [HUB_EDITORS]
    try:
        if other := json.loads(HUB_OTHER.read_text()):
            places.append(Path(other))
    except (OSError, ValueError):
        pass
    return next((app for place in places if (app := place / wanted / "Unity.app").is_dir()), None)


def _processes() -> str:
    """Every process with its command line, or none where they can't be listed: in a sandbox, such as a bot's own
    scratch daemon, `ps` isn't allowed, and there are no Editors of its to find."""
    try:
        return subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True).stdout
    except OSError:
        return ""


def editors() -> dict[Path, int]:
    """The open Editors, by the folder each has open: not their asset import workers, which run in batch mode."""
    found = {}
    for line in _processes().splitlines():
        pid, _, args = line.strip().partition(" ")
        if EDITOR in args and "-batchmode" not in args.lower() and (folder := PROJECT.search(args)):
            found[Path(folder[1]).resolve()] = int(pid)
    return found


def open_editor(app: Path, folder: Path) -> None:
    subprocess.run(["defaults", "write", PREFS, AUTO_START, "-int", "1"], capture_output=True)
    subprocess.run(["open", "-n", "-g", "-a", str(app), "--args", "-projectPath", str(folder)], check=True,
                   capture_output=True)


def close_editor(pid: int, wait_s: float = 10) -> None:
    """Quits the Editor: what it hadn't saved is lost, as when Unity is quit from the Dock without saving."""
    try:
        os.kill(pid, signal.SIGTERM)
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            os.kill(pid, 0)
            time.sleep(0.2)
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def connected(url: str, name: str) -> bool:
    """Whether an Editor of a project called `name` is connected to the Unity MCP server at `url`."""
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=20) as client:
        async def call(method: str, params: dict | None = None, id: int | None = 1, session: str | None = None):
            body = {"jsonrpc": "2.0", "method": method} | ({"params": params} if params is not None else {}) | (
                {"id": id} if id else {})
            answer = await client.post(url, json=body, headers=headers | ({"Mcp-Session-Id": session} if session else {}))
            text = answer.text
            if "event-stream" in answer.headers.get("content-type", ""):
                text = next((line[5:] for line in reversed(text.splitlines()) if line.startswith("data:")), "")
            return answer.headers.get("mcp-session-id") or session, json.loads(text) if text.strip() else None

        session, _ = await call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                               "clientInfo": {"name": "crewbots", "version": "1"}})
        await call("notifications/initialized", id=None, session=session)
        _, read = await call("resources/read", {"uri": "mcpforunity://instances"}, id=2, session=session)
        listed = json.loads(read["result"]["contents"][0]["text"])
        return any(i.get("name") == name for i in listed.get("instances", []))
