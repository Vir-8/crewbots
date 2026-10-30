"""Reading and writing the crew's own files from the app: role cards in `bots/` and routines in `routines/`.

The files stay the source of truth, and what's written is exactly what the crew reads: every change is checked by
the same parser the daemon uses before it replaces a file, so a bad edit is refused instead of breaking the crew.
Frontmatter the app doesn't edit (a bot's MCP servers, say) is kept as it was. YAML comments are not.
"""

import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from crewbots import frontmatter
from crewbots import routines as routines_module
from crewbots.crew import CrewError, _context_window, parse_card

HANDLE = re.compile(r"[a-z][a-z0-9-]{0,30}")
BOT_FIELDS = ("display_name", "title", "description", "backend", "model", "effort", "context_window", "access", "network",
              "avatar", "budget", "mcp", "skills", "can_delegate", "proactive")
ROUTINE_FIELDS = ("description", "bot", "schedule", "on", "repo", "workspace", "budget", "enabled", "proposed_by")


def read(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text()
    if not text.startswith("---"):
        return {}, text
    if (parts := frontmatter.split(text)) is None:
        raise CrewError(f"{path.name}: the frontmatter has no closing ---")
    raw, body = parts
    return yaml.safe_load(raw) or {}, body.lstrip("\n")


def card(home: Path, name: str) -> dict[str, Any]:
    meta, body = read(_bot_path(home, name))
    out = {"name": name, **{k: meta.get(k) for k in BOT_FIELDS}, "instructions": body}
    if isinstance(out.get("mcp"), dict):  # servers defined on the card itself: the app shows them by name
        out["mcp"] = sorted(out["mcp"])
    if out.get("context_window") not in (None, "auto"):  # written by hand as "200k", say: the app shows tokens
        try:
            out["context_window"] = _context_window(out["context_window"], name)
        except CrewError:
            out["context_window"] = None
    return out


def save_card(home: Path, name: str, fields: dict[str, Any], instructions: str | None, create: bool = False) -> Path:
    """Updates a role card's editable fields and its instructions (the body), or writes a new bot's card."""
    if create:
        if not HANDLE.fullmatch(name):
            raise CrewError("a bot's handle is lowercase letters, digits and dashes, starting with a letter: it's how "
                            "you and other bots @mention it")
        path = home / "bots" / f"{name}.md"
        if path.exists():
            raise CrewError(f"there's already a bot called {name}")
        meta, body = {}, ""
    else:
        path = _bot_path(home, name)
        meta, body = read(path)
    for key in BOT_FIELDS:
        if key in fields:
            value = fields[key]
            if value in (None, "", [], {}) or (key == "network" and value is False) or (key == "proactive" and value is True):
                meta.pop(key, None)
            else:
                meta[key] = value
    text = _compose(meta, instructions if instructions is not None else body)
    parse_card(text, default_name=name, source=path.name)  # refuse anything the crew couldn't load
    _write(path, text)
    return path


def routine(home: Path, name: str) -> dict[str, Any]:
    meta, body = _read_routine(_routine_path(home, name))
    return {"name": name, **{k: meta.get(k) for k in ROUTINE_FIELDS}, "goal": body.strip()}


def save_routine(home: Path, name: str, fields: dict[str, Any], goal: str | None, create: bool = False) -> Path:
    if create:
        if not HANDLE.fullmatch(name):
            raise CrewError("a routine's name is lowercase letters, digits and dashes, starting with a letter")
        path = home / "routines" / f"{name}.md"
        if path.exists():
            raise CrewError(f"there's already a routine called {name}")
        meta, body = {}, ""
    else:
        path = _routine_path(home, name)
        meta, body = _read_routine(path)
    for key in ROUTINE_FIELDS:
        if key in fields:
            value = fields[key]
            if value in (None, "") or (key == "enabled" and value is True):
                meta.pop(key, None)  # enabled is the default
            else:
                meta[key] = value
    text = _compose(meta, goal if goal is not None else body)
    routines_module.parse(text, name, path)  # refuse anything the scheduler couldn't run
    _write(path, text)
    return path


def delete_routine(home: Path, name: str) -> None:
    _routine_path(home, name).unlink()


def _read_routine(path: Path) -> tuple[dict[str, Any], str]:
    meta, body = read(path)
    if True in meta:  # YAML reads a bare `on:` key as true
        meta["on"] = meta.pop(True)
    return meta, body


def _compose(meta: dict[str, Any], body: str) -> str:
    raw = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=120)
    raw = raw.replace("'on':", "on:")  # keep the routine key readable as `on:`
    return f"---\n{raw}---\n{body.strip()}\n"


def _write(path: Path, text: str) -> None:
    """All or nothing: the daemon reloads on change, and must never read half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".edit-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _bot_path(home: Path, name: str) -> Path:
    for path in sorted((home / "bots").glob("*.md")):
        meta, _ = read(path)
        if str(meta.get("name", path.stem)) == name:
            return path
    raise KeyError(name)


def _routine_path(home: Path, name: str) -> Path:
    path = home / "routines" / f"{name}.md"
    if not HANDLE.fullmatch(name) or not path.exists():
        raise KeyError(name)
    return path
