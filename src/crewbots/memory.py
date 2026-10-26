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
work, and `recall` searches the logs and notes along with the crew's record. The files are the source of truth: edit
or delete one and the rest follows.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

CREW_SCOPE = "crew"
INDEX_LIMIT = 60  # notes listed in a bot's instructions per scope; older ones stay in the folder
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

    @property
    def gist(self) -> str:
        first = next((line.strip() for line in self.body.splitlines() if line.strip()), "")
        return first if len(first) <= 140 else first[:139] + "…"


class MemoryError(ValueError):
    pass


class Memory:
    def __init__(self, crew_home: Path):
        self.root = crew_home / "memory"

    def notes(self, scope: str) -> list[Note]:
        folder = self._notes_dir(scope)
        if not folder.is_dir():
            return []
        notes = [n for n in (_read(p) for p in folder.glob("*.md")) if n]
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
        meta = {"title": title, "created": created, "updated": _today(), "source": source}
        path.write_text(f"---\n{yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)}---\n{body.strip()}\n")
        self._write_index(scope)
        return _read(path)  # type: ignore[return-value]

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
        now = datetime.now().astimezone()
        path = self._scope_dir(scope) / "daily" / f"{now.date().isoformat()}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a") as f:
            f.write((f"# {now.date().isoformat()}\n\n" if new else "") + f"- {now:%H:%M} {' '.join(line.split())}\n")

    def recent(self, scope: str) -> str | None:
        """The scope's logs for the last RECENT_DAYS days, oldest first."""
        folder = self._scope_dir(scope) / "daily"
        today = datetime.now().astimezone().date()
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
            meta = yaml.safe_load(raw) or {}
        except (ValueError, yaml.YAMLError):
            meta = {}
    return Note(name=path.stem, title=str(meta.get("title") or path.stem), body=body.strip(),
                created=str(meta.get("created") or ""), source=str(meta.get("source") or ""), path=path)


def _slug(title: str) -> str:
    slug = NAME.sub("-", title.lower()).strip("-")[:60].strip("-")
    if not slug:
        raise MemoryError("a note's title needs at least one letter or digit")
    return slug


def _today() -> str:
    return datetime.now(UTC).date().isoformat()
