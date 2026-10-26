"""What bots remember across tasks, as files you can read, edit and delete.

    crew/memory/<bot>/notes/<name>.md   one note per fact, with when and where it was learned
    crew/memory/<bot>/MEMORY.md         the index, regenerated from the notes; it's what the bot sees every run
    crew/memory/crew/...                the same, for things every bot should know

A bot writes with the `remember` tool and removes with `forget`. Every run, its instructions list its notes and the
crew's (title, first line, path), and it reads a note's file when a task needs it, the way skills work. The notes are
the source of truth: edit or delete one and the index follows.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

CREW_SCOPE = "crew"
INDEX_LIMIT = 60  # notes listed in a bot's instructions per scope; older ones stay in the folder
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


MEMORY_GUIDE = """# Memory

Your notes, and the crew's, outlast this task. Use them before asking the user something they may already have told
the crew, and read a note's file when it bears on the task.

- `remember(title, body, shared?)`: keep something that will still be true and useful next time: what the user prefers,
  a decision and its reason, a pitfall in this codebase, how something here is done. One fact per note, with a short
  title. Not task progress (that's the handoff) and not anything a file in the repo already says. `shared: true` puts
  it in the crew's notes, for every bot. Remembering the same title again updates that note.
- `forget(name, shared?)`: remove a note that's wrong or out of date.
- `recall(query)`: search everything the crew has done (messages, replies, handoffs, reviews, questions) for what was
  said or decided before. Results carry task ids."""


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
