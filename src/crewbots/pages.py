"""A bot's pages: its scratchpad, and the pages that grow from it, as Markdown files in its own folder
(~/Documents/Crewbots/<bot>), which the user reads and edits too: in the app, or in any editor. Pages link to each
other with [[Page name]], as in Obsidian, and a page can sit in a subfolder ([[Projects/Sky Islands]]).

The scratchpad, Scratchpad.md, is where a bot keeps what it's tracking: what's top of mind, what's in flight, what
waits on the user. It's in the bot's instructions in every session, so the bot always has it in view, and an edit
the user makes reaches it in its next turn. Other pages are listed there by name and read when a task needs them.
"""

import re
from dataclasses import dataclass
from pathlib import Path

SCRATCHPAD = "Scratchpad"
SCRATCHPAD_CHARS = 8000  # of the scratchpad in a bot's instructions, about 2K tokens: past that it's cut
PAGE_CHARS = 200_000  # a page longer than this isn't a page
LISTED = 40  # other pages named in a bot's instructions, the latest changed first
NAME = re.compile(r"[\w][\w ,.'()&+-]*(/[\w][\w ,.'()&+-]*)*", re.UNICODE)
LINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")


class PageError(ValueError):
    pass


@dataclass(frozen=True)
class Page:
    name: str  # its path in the folder, without .md: "Scratchpad", "Projects/Sky Islands"
    path: Path
    updated: float


def pages(folder: Path) -> list[Page]:
    """The folder's pages, the scratchpad first and then the latest changed. Hidden folders aren't looked in."""
    if not folder.is_dir():
        return []
    found = [Page(_name(folder, p), p, p.stat().st_mtime) for p in folder.rglob("*.md")
             if not any(part.startswith(".") for part in p.relative_to(folder).parts)]
    found.sort(key=lambda p: (p.name != SCRATCHPAD, -p.updated))
    return found


def read(folder: Path, name: str) -> str:
    path = _path(folder, name)
    if not path.is_file():
        raise PageError(f"no page named {name!r}")
    return path.read_text(errors="replace")


def write(folder: Path, name: str, text: str) -> Page:
    """Writes a page whole, making it if it's new."""
    if len(text) > PAGE_CHARS:
        raise PageError(f"a page holds at most {PAGE_CHARS:,} characters; split it into linked pages")
    path = _path(folder, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n")
    return Page(_name(folder, path), path, path.stat().st_mtime)


def edit(folder: Path, name: str, find: str, replace: str) -> Page:
    """Replaces one exact piece of a page: `find` must be in it once."""
    text = read(folder, name)
    count = text.count(find) if find else 0
    if count != 1:
        raise PageError(f"{'not found' if count == 0 else f'found {count} times'} in {name}: give text that's in it "
                        "exactly once, or write the page whole")
    return write(folder, name, text.replace(find, replace, 1))


def resolve(folder: Path, link: str) -> str | None:
    """The page a [[link]] names: by its whole name, or by its own name wherever it is, ignoring case."""
    wanted = link.strip().removesuffix(".md").lower()
    named = pages(folder)
    for page in named:
        if page.name.lower() == wanted:
            return page.name
    return next((p.name for p in named if p.name.rsplit("/", 1)[-1].lower() == wanted.rsplit("/", 1)[-1]), None)


def for_instructions(folder: Path) -> str:
    """The scratchpad, and the other pages by name, as a bot's instructions hold them."""
    found = pages(folder)
    scratch = next((p for p in found if p.name == SCRATCHPAD), None)
    text = scratch.path.read_text(errors="replace").strip() if scratch else ""
    if len(text) > SCRATCHPAD_CHARS:
        text = text[:SCRATCHPAD_CHARS] + "\n\n(cut here: it's longer than fits. Move detail to linked pages.)"
    others = [p.name for p in found if p.name != SCRATCHPAD][:LISTED]
    listed = f"\n\nYour other pages: {', '.join(f'[[{n}]]' for n in others)}." if others else ""
    return (text or "(empty: you haven't started it yet)") + listed


def _path(folder: Path, name: str) -> Path:
    name = name.strip().removesuffix(".md")
    if not NAME.fullmatch(name) or ".." in name:
        raise PageError(f"{name!r} isn't a page name: words, with / for a subfolder")
    path = (folder / f"{name}.md").resolve()
    if not path.is_relative_to(folder.resolve()):
        raise PageError(f"{name!r} is outside your pages")
    return path


def _name(folder: Path, path: Path) -> str:
    return str(path.relative_to(folder).with_suffix(""))
