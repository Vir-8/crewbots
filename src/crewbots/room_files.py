"""Group chats a crew has from the start: Markdown files in the crew's `rooms/` folder, like routines. The frontmatter
names the chat, its lead and its bots; the body says what it's for, which its bots read with the chat's setting.

    ---
    title: Engineering
    lead: eng-lead
    members: [eng-lead, engineer-1, engineer-2, reviewer]
    ---
    Where engineering goals are planned, built, reviewed and merged.

Each is made once, when the daemon starts or the crew's files change. Renamed, changed, archived or deleted in the app
after that, it stays as the user left it. A crew that's started over gets them again.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

FIELDS = {"title", "lead", "members"}


class RoomFileError(ValueError):
    pass


@dataclass(frozen=True)
class RoomFile:
    name: str  # the file's name, which marks the chat as made
    title: str
    lead: str
    members: list[str]
    about: str


def load(folder: Path, bots: set[str]) -> tuple[list[RoomFile], dict[str, str]]:
    """The crew's group chat files, and the ones that couldn't be read (file name → why)."""
    rooms, errors = [], {}
    if not folder.is_dir():
        return rooms, errors
    for path in sorted(folder.glob("*.md")):
        try:
            rooms.append(parse(path.read_text(), path.stem, bots))
        except RoomFileError as e:
            errors[path.name] = str(e)
    return rooms, errors


def parse(text: str, name: str, bots: set[str]) -> RoomFile:
    if not text.startswith("---"):
        raise RoomFileError("a group chat file starts with --- frontmatter")
    try:
        _, raw, body = text.split("---", 2)
    except ValueError:
        raise RoomFileError("the frontmatter has no closing ---") from None
    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise RoomFileError(f"the frontmatter isn't valid YAML: {e}") from None
    if not isinstance(meta, dict):
        raise RoomFileError("the frontmatter must be a mapping")
    if unknown := set(meta) - FIELDS:
        raise RoomFileError(f"unknown fields {sorted(unknown)}; a group chat file has {sorted(FIELDS)}")
    members = meta.get("members")
    if not isinstance(members, list) or not members:
        raise RoomFileError("members lists the bots in the group chat")
    members = list(dict.fromkeys(str(m) for m in members))
    if missing := [m for m in members if m not in bots]:
        raise RoomFileError(f"no bots named {missing}; the crew is {sorted(bots)}")
    lead = str(meta.get("lead") or members[0])
    if lead not in members:
        raise RoomFileError(f"the lead, {lead}, isn't one of its members")
    return RoomFile(name=name, title=str(meta.get("title") or name), lead=lead, members=members, about=body.strip())
