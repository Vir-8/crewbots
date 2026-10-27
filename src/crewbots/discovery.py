"""What the user's other coding agents already know about their work, for getting a crew started: the folders they've
worked in with Claude Code or Codex lately, offered as projects, and the memories those keep (Claude Code's auto
memory, Codex's memories), for the chief to learn from.

Only a folder's path is taken from the CLIs' session files, never what was said in them. Nothing comes in until the
user picks it.
"""

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from crewbots import paths
from crewbots.projects import name_for

DAYS = 180  # how far back a folder counts as one the user works in
MOST = 20  # folders offered
LINES = 60  # of a Claude Code session file, read for its folder
MEMORY_CHARS = 400_000  # of memories handed to the chief at once (about 100K tokens): past this, they're left out
DATED = re.compile(r"\d{4}-\d{2}-\d{2}")  # Codex's folders for chats without a project: ~/Documents/Codex/<date>/...


@dataclass
class FoundMemory:
    path: Path
    source: str  # claude or codex
    title: str
    folder: Path | None = None  # the folder it's about, for Claude Code's per-folder memory


@dataclass
class FoundFolder:
    folder: Path
    sources: set[str] = field(default_factory=set)
    sessions: int = 0
    last_used: float = 0.0
    memories: list[FoundMemory] = field(default_factory=list)
    name: str = ""  # as a project: its folder's name, or with its parent's too where two folders share one

    @property
    def about(self) -> str:
        return _about(self.folder)


def found(days: int = DAYS) -> tuple[list[FoundFolder], list[FoundMemory]]:
    """The folders worked in lately, most recent first, and every memory found: each folder's own, and the ones
    that aren't any folder's (Codex keeps one set for everything)."""
    since = time.time() - days * 86_400
    folders: dict[Path, FoundFolder] = {}

    roots: dict[Path, Path | None] = {}  # each folder's project, worked out once

    def note(folder: Path, source: str, when: float, sessions: int = 1, memories: list[FoundMemory] = ()) -> None:
        top = roots[folder] if folder in roots else roots.setdefault(folder, _project_root(folder))
        if top is None:
            return
        entry = folders.setdefault(top, FoundFolder(top))
        entry.sources.add(source)
        entry.sessions += sessions
        entry.last_used = max(entry.last_used, when)
        entry.memories += [m for m in memories if m.path not in {e.path for e in entry.memories}]

    for folder, when, sessions, memories in _claude_folders(since):
        note(folder, "claude", when, sessions, memories)
    for folder, when in _codex_sessions(since):
        note(folder, "codex", when)
    ordered = sorted(folders.values(), key=lambda f: f.last_used, reverse=True)[:MOST]
    names = [name_for(f.folder) for f in ordered]
    for entry, name in zip(ordered, names, strict=True):
        entry.name = name if names.count(name) == 1 else name_for(Path(f"{entry.folder.parent.name} {entry.folder.name}"))
    return ordered, _codex_memories()


def digest(memories: list[FoundMemory], limit: int = MEMORY_CHARS) -> tuple[str, dict[Path, str]]:
    """The memories' text, each under where it came from, up to `limit` characters; and the fingerprint of each one
    that went in whole, by its path."""
    parts, used, taken = [], 0, {}
    for memory in memories:
        try:
            text = memory.path.read_text(errors="replace")
        except OSError:
            continue
        where = f"{'Claude Code' if memory.source == 'claude' else 'Codex'}: {_shown(memory.path)}"
        block = f"## {where}\n\n{text.strip()}\n"
        if used + len(block) > limit:
            parts.append(f"## {where}\n\n(left out: there was more than fits)\n")
            continue
        parts.append(block)
        used += len(block)
        taken[memory.path] = fingerprint(text)
    return "\n".join(parts), taken


def fingerprint(text: str) -> str:
    """What a memory said, in a few characters: when they change, it has."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def fingerprints(memories: list[FoundMemory]) -> dict[Path, str]:
    """Each readable memory's fingerprint, by its path."""
    out = {}
    for memory in memories:
        try:
            out[memory.path] = fingerprint(memory.path.read_text(errors="replace"))
        except OSError:
            continue
    return out


def _claude_folders(since: float) -> list[tuple[Path, float, int, list[FoundMemory]]]:
    """Claude Code keeps each folder's sessions in ~/.claude/projects/<folder, flattened>/, with its auto memory in
    memory/ there. The flattened name can't be turned back into the path, so the path comes from a session file."""
    root = paths.home() / ".claude" / "projects"
    out = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        sessions = [p for p in folder.glob("*.jsonl") if p.stat().st_mtime >= since]
        if not sessions:
            continue
        sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        cwd = next((c for c in map(_claude_cwd, sessions[:3]) if c), None)
        if cwd is None:
            continue
        memories = [FoundMemory(p, "claude", _title(p), cwd) for p in sorted((folder / "memory").glob("*.md"))]
        out.append((cwd, sessions[0].stat().st_mtime, len(sessions), memories))
    return out


def _claude_cwd(session: Path) -> Path | None:
    try:
        with session.open(errors="replace") as lines:
            for n, line in enumerate(lines):
                if n >= LINES:
                    break
                if '"cwd"' not in line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if cwd:
                    return Path(cwd)
    except OSError:
        return None
    return None


def _codex_sessions(since: float) -> list[tuple[Path, float]]:
    """Codex keeps a file per session in ~/.codex/sessions/<year>/<month>/<day>/, its folder on the first line."""
    root = paths.codex_home() / "sessions"
    cutoff = time.strftime("%Y/%m/%d", time.localtime(since))
    out = []
    for day in sorted(root.glob("*/*/*")) if root.is_dir() else []:
        if "/".join(day.parts[-3:]) < cutoff:
            continue
        for session in day.glob("rollout-*.jsonl"):
            try:
                with session.open(errors="replace") as lines:
                    meta = json.loads(lines.readline())
                cwd = (meta.get("payload") or {}).get("cwd")
            except (OSError, ValueError, AttributeError):
                continue
            if cwd:
                out.append((Path(cwd), session.stat().st_mtime))
    return out


def _codex_memories() -> list[FoundMemory]:
    """Codex's memories, when the user has them on: its summary and its handbook, about everything they've done."""
    root = paths.codex_home() / "memories"
    return [FoundMemory(root / name, "codex", title) for name, title in
            (("memory_summary.md", "Codex's summary of you and your work"), ("MEMORY.md", "Codex's memory handbook"))
            if (root / name).is_file()]


def _project_root(folder: Path) -> Path | None:
    """The folder a session ran in, as a project: the top of its git repository if it's in one. None for a folder
    that's gone, or isn't the user's work: temporary folders, the apps' own scratch folders, and crewbots' own."""
    try:
        folder = folder.expanduser().resolve()
    except OSError:
        return None
    home = paths.home().resolve()
    skipped = [paths.root(), paths.bot_folders(), paths.home() / ".claude", paths.codex_home(), paths.home() / "Library"]
    if (not folder.is_dir() or folder == home or not folder.is_relative_to(home)
            or any(folder.is_relative_to(s.expanduser().resolve()) for s in skipped)
            or (folder.is_relative_to(home / "Documents" / "Codex") and any(DATED.fullmatch(p) for p in folder.parts))):
        return None
    try:
        top = subprocess.run(["git", "-C", str(folder), "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        top = ""
    return Path(top).resolve() if top else folder


def _about(folder: Path) -> str:
    """What a folder is, in a line: its README's first sentence that isn't a heading, a badge or a picture."""
    for name in ("README.md", "readme.md", "README", "AGENTS.md", "CLAUDE.md"):
        try:
            text = (folder / name).read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "!", "[!", "<", "[![", "---", "```", "|", ">")):
                line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line).replace("**", "").replace("`", "")
                first = re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0]
                return first if len(first) <= 200 else first[:199] + "…"
    return ""


def _title(memory: Path) -> str:
    """A memory file's name for it: its frontmatter `description` or `name`, or its file name."""
    if memory.name == "MEMORY.md":
        return "Its index"
    try:
        text = memory.read_text(errors="replace")
        meta = yaml.safe_load(text[3:].split("\n---", 1)[0]) if text.startswith("---") else None
    except (OSError, yaml.YAMLError):
        meta = None
    if isinstance(meta, dict):
        for key in ("description", "name"):
            if meta.get(key):
                return " ".join(str(meta[key]).split())
    return memory.stem


def _shown(path: Path) -> str:
    return str(path).replace(str(paths.home()), "~", 1)
