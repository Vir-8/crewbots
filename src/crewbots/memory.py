"""What bots remember across tasks, as files you can read, edit and delete.

    crew/memory/<bot>/CORE.md                 what matters most, pinned in every session; kept short
    crew/memory/<bot>/notes/<name>.md         one note per fact, with when and where it was learned
    crew/memory/<bot>/MEMORY.md               the notes' index, regenerated from them; every session lists it
    crew/memory/<bot>/daily/YYYY-MM-DD.md     the day's log: what the bot finished, and what it set down
    crew/memory/<bot>/state/<task id>.md      where one task stands: its goal, decisions, open threads, exact ids
    crew/memory/crew/...                      the same, for things every bot should know

A bot writes notes with the `remember` tool and removes them with `forget`, and keeps its task's state with
`update_state`. Every session starts with its CORE, its notes' index (title, first line, path), its last two days'
logs and, for a task it has worked on, that task's state; it reads a note's file when a task needs it, the way skills
work, and `recall` searches the logs and notes along with the crew's record. Overnight, consolidation (the runner's
dreams) supersedes, merges, shares and rewrites them, and commits the folder, its own git repository. The files are
the source of truth: edit or delete one and the rest follows.
"""

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from crewbots import paths, timefmt

CREW_SCOPE = "crew"
INDEX_LIMIT = 60  # notes listed in a bot's instructions per scope; older ones stay in the folder
SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # PyYAML's C one when it has it: notes are read often
CORE_CHARS = 6000  # of each CORE.md, about 1.5K tokens: past that it's cut, since it's in every session
STATE_CHARS = 8000  # of a task's state
RECENT_DAYS = 2  # of daily logs a fresh session starts with: today's and yesterday's
NAME = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Note:
    name: str
    title: str
    body: str
    created: str
    source: str
    path: Path
    updated: str = ""
    superseded_by: str = ""  # the note that replaced it: it's kept, for history, but no longer listed

    @property
    def gist(self) -> str:
        first = next((line.strip() for line in self.body.splitlines() if line.strip()), "")
        return first if len(first) <= 140 else first[:139] + "…"


class MemoryError(ValueError):
    pass


class Memory:
    def __init__(self, crew_home: Path):
        self.root = crew_home / "memory"

    def notes(self, scope: str, superseded: bool = False) -> list[Note]:
        """The scope's notes, newest first: those still current, or with `superseded`, every one."""
        folder = self._notes_dir(scope)
        if not folder.is_dir():
            return []
        notes = [n for n in (_read(p) for p in folder.glob("*.md")) if n and (superseded or not n.superseded_by)]
        return sorted(notes, key=lambda n: n.created, reverse=True)

    def remember(self, scope: str, title: str, body: str, source: str) -> Note:
        title = " ".join(title.split())
        if not title:
            raise MemoryError("a note needs a title")
        folder = self._notes_dir(scope)
        folder.mkdir(parents=True, exist_ok=True)
        name = _slug(title)
        path = folder / f"{name}.md"
        if path.exists():  # the same title again: an update, not a second copy
            old = _read(path)
            created = old.created if old else _today()
        else:
            created = _today()
        _write(path, {"title": title, "created": created, "updated": _today(), "valid_from": created, "source": source},
               body)
        self._write_index(scope)
        return _read(path)  # type: ignore[return-value]

    def supersede(self, scope: str, name: str, by: str) -> Note:
        """Marks a note as replaced by note `by`: it stays, with when it stopped being true, but drops out of the
        index and every session."""
        path = self._notes_dir(scope) / f"{_slug(name)}.md"
        note = _read(path)
        if note is None:
            raise MemoryError(f"no note named {name!r}")
        meta = yaml.safe_load(path.read_text().split("---", 2)[1]) or {}
        _write(path, {**meta, "superseded_by": by, "valid_until": _today()}, note.body)
        self._write_index(scope)
        return _read(path)  # type: ignore[return-value]

    def promote(self, scope: str, name: str) -> Note:
        """Moves one of a bot's notes to the crew's, for every bot."""
        note = _read(self._notes_dir(scope) / f"{_slug(name)}.md")
        if note is None:
            raise MemoryError(f"no note named {name!r}")
        shared = self.remember(CREW_SCOPE, note.title, note.body, f"{note.source} (from {scope})")
        self.forget(scope, name)
        return shared

    def changed_since(self, scope: str, day: str) -> list[Note]:
        """Current notes written or updated on `day` (YYYY-MM-DD) or later."""
        return [n for n in self.notes(scope) if (n.updated or n.created) >= day]

    def logs_since(self, scope: str, day: str) -> str:
        """The scope's daily logs from `day` (YYYY-MM-DD) on, oldest first."""
        folder = self._scope_dir(scope) / "daily"
        logs = sorted(p for p in folder.glob("*.md") if p.stem >= day) if folder.is_dir() else []
        return "\n\n".join(p.read_text().strip() for p in logs)

    def write_core(self, scope: str, text: str) -> Path:
        """Consolidation's CORE.md for the scope, cut to CORE_CHARS."""
        path = self._scope_dir(scope) / "CORE.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = text.strip()
        path.write_text((text if len(text) <= CORE_CHARS else text[:CORE_CHARS].rstrip()) + "\n")
        return path

    def commit(self, message: str) -> str | None:
        """Commits the memory folder, its own git repository (made the first time), so every change can be looked
        back on and undone. Returns the commit, or None if nothing changed."""
        if not self.root.is_dir():
            return None
        git = [paths.git(), "-C", str(self.root), "-c", "user.name=crewbots", "-c", "user.email=crewbots@localhost",
               "-c", "commit.gpgsign=false"]
        if not (self.root / ".git").exists():
            subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
        subprocess.run([*git, "add", "-A", "--", ".", ":(exclude,glob)**/.state.lock",
                        ":(exclude,glob)**/working-state/.state-*"], check=True, capture_output=True)
        if not subprocess.run([*git, "diff", "--cached", "--quiet"], capture_output=True).returncode:
            return None
        subprocess.run([*git, "commit", "-q", "-m", message], check=True, capture_output=True)
        return subprocess.run([*git, "rev-parse", "--short", "HEAD"], check=True, capture_output=True,
                              text=True).stdout.strip()

    def forget(self, scope: str, name: str) -> Note:
        path = self._notes_dir(scope) / f"{_slug(name)}.md"
        note = _read(path)
        if note is None:
            known = ", ".join(n.name for n in self.notes(scope)) or "none"
            raise MemoryError(f"no note named {name!r} (notes: {known})")
        path.unlink()
        self._write_index(scope)
        return note

    def prompt(self, bot: str) -> str | None:
        """The memory section of a bot's instructions: its notes and the crew's, as an index."""
        sections = []
        for scope, heading in ((bot, "Your notes"), (CREW_SCOPE, "The crew's notes")):
            notes = self.notes(scope)
            if not notes:
                continue
            lines = [f"- {n.title}: {n.gist} ({n.path})" for n in notes[:INDEX_LIMIT]]
            if len(notes) > INDEX_LIMIT:
                lines.append(f"- … and {len(notes) - INDEX_LIMIT} older notes in {self._notes_dir(scope)}")
            sections.append(f"## {heading}\n\n" + "\n".join(lines))
        if not sections:
            return None
        return "\n\n".join(sections)

    def core(self, scope: str) -> str | None:
        """The scope's CORE.md, cut to CORE_CHARS. Consolidation writes it (or you do)."""
        try:
            text = (self._scope_dir(scope) / "CORE.md").read_text().strip()
        except OSError:
            return None
        return (text if len(text) <= CORE_CHARS else text[:CORE_CHARS].rstrip() + "\n…") or None

    def log(self, scope: str, line: str) -> None:
        """Adds a line to the scope's log for today."""
        now = timefmt.now()
        path = self._scope_dir(scope) / "daily" / f"{now.date().isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a") as f:
            f.write((f"# {now.date().isoformat()}\n\n" if new else "") + f"- {now:%H:%M} {' '.join(line.split())}\n")

    def recent(self, scope: str) -> str | None:
        """The scope's logs for the last RECENT_DAYS days, oldest first."""
        folder = self._scope_dir(scope) / "daily"
        today = timefmt.now().date()
        days = [(today - timedelta(days=n)).isoformat() for n in range(RECENT_DAYS - 1, -1, -1)]
        logs = [(folder / f"{day}.md").read_text().strip() for day in days if (folder / f"{day}.md").is_file()]
        return "\n\n".join(logs) or None

    def state(self, scope: str, task_id: str) -> str | None:
        try:
            return (self._scope_dir(scope) / "state" / f"{task_id}.md").read_text().strip() or None
        except OSError:
            return None

    def set_state(self, scope: str, task_id: str, text: str) -> Path:
        """Writes where a task stands, whole: it replaces the last."""
        if len(text) > STATE_CHARS:
            raise MemoryError(f"keep the state under {STATE_CHARS} characters: what's needed to carry on, not a log")
        if not re.fullmatch(r"t_[0-9a-f]+", task_id):
            raise MemoryError(f"bad task id {task_id!r}")
        path = self._scope_dir(scope) / "state" / f"{task_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.strip() + "\n")
        return path

    def search(self, scopes: list[str], query: str, limit: int = 4) -> list[dict[str, str]]:
        """Lines in the scopes' daily logs, and notes, that have every word of `query`, or else any of them; the
        newest first."""
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
        if not words:
            return []
        found: list[tuple[int, dict[str, str]]] = []
        for scope in scopes:
            folder = self._scope_dir(scope)
            for path in sorted(folder.glob("daily/*.md"), reverse=True) + sorted(folder.glob("notes/*.md")):
                kind = "log" if path.parent.name == "daily" else "note"
                try:
                    lines = path.read_text().splitlines()
                except OSError:
                    continue
                for line in lines:
                    text = line.lower()
                    score = sum(w in text for w in words)
                    if score and not line.startswith(("#", "---", "title:", "created:", "updated:", "source:")):
                        found.append((score, {"scope": scope, "kind": kind, "day": path.stem if kind == "log" else "",
                                              "path": str(path), "text": line.strip("- ").strip()}))
        best = max((score for score, _ in found), default=0)
        return [hit for score, hit in found if score == best][:limit]

    def _scope_dir(self, scope: str) -> Path:
        return self._notes_dir(scope).parent

    def _notes_dir(self, scope: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", scope):
            raise MemoryError(f"bad memory scope {scope!r}")
        return self.root / scope / "notes"

    def _write_index(self, scope: str) -> None:
        notes = self.notes(scope)
        index = self.root / scope / "MEMORY.md"
        lines = [f"# Memory: {scope}", "", "Generated from notes/; edit or delete a note there and this follows.", ""]
        lines += [f"- [{n.title}](notes/{n.name}.md): {n.gist}" for n in notes]
        index.write_text("\n".join(lines) + "\n")


def _read(path: Path) -> Note | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    meta: dict = {}
    body = text
    if text.startswith("---"):
        try:
            _, raw, body = text.split("---", 2)
            meta = yaml.load(raw, Loader=SAFE_LOADER) or {}
        except (ValueError, yaml.YAMLError):
            meta = {}
    return Note(name=path.stem, title=str(meta.get("title") or path.stem), body=body.strip(),
                created=str(meta.get("created") or ""), source=str(meta.get("source") or ""), path=path,
                updated=str(meta.get("updated") or ""), superseded_by=str(meta.get("superseded_by") or ""))


def _write(path: Path, meta: dict, body: str) -> None:
    path.write_text(f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}---\n{body.strip()}\n")


def _slug(title: str) -> str:
    slug = NAME.sub("-", title.lower()).strip("-")[:60].strip("-")
    if not slug:
        raise MemoryError("a note's title needs at least one letter or digit")
    return slug


def _today() -> str:
    return datetime.now(UTC).date().isoformat()
