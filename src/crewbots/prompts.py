"""The words crewbots gives the models, each in a markdown file you can read and change.

The defaults are packaged in `crewbots/prompts/`: the crew's tools and settings, the memory guide, the briefing and
retry turns, the review prompts, and each crew tool's description (`tools/<name>.md`). A crew replaces any of them
with a file at the same path under its own folder: `~/.crewbots/crew/prompts/review/lead.md` replaces
`review/lead.md`, and deleting it brings the default back.

`{name}` in a prompt is filled in from what the code passes (the review prompts take `{how_to_review}` and
`{line_comment}`); braces around anything else are left as they are. A file is read when it's first needed and again
whenever it changes, so an edit counts from the next turn, with nothing to restart. The order prompts are put
together in doesn't change: what stays the same comes first, for the models' prompt caches.
"""

import re
from importlib import resources
from pathlib import Path

from crewbots import paths

PACKAGED = Path(str(resources.files("crewbots") / "prompts"))
FOLDER = "prompts"  # in a crew's folder, for its own versions
PLACEHOLDER = re.compile(r"\{(\w+)\}")

_read: dict[Path, tuple[int, str]] = {}  # a file → (its modification time, its text)


def text(name: str, home: Path | None = None, **values: object) -> str:
    """The prompt `name` (such as "review/lead"), from the crew's own copy if it has one, with `values` filled in."""
    return PLACEHOLDER.sub(lambda m: str(values[m[1]]) if m[1] in values else m[0], _load(name, home))


def path(name: str, home: Path | None = None) -> Path:
    """The file `name` comes from now: the crew's own, or the packaged default."""
    own = (home or paths.crew()) / FOLDER / f"{name}.md"
    return own if own.is_file() else PACKAGED / f"{name}.md"


def names() -> list[str]:
    """Every prompt there is, by name."""
    return sorted(str(p.relative_to(PACKAGED).with_suffix("")) for p in PACKAGED.rglob("*.md"))


def _load(name: str, home: Path | None) -> str:
    source = path(name, home)
    try:
        stamp = source.stat().st_mtime_ns
    except OSError:
        raise KeyError(f"no prompt named {name!r}") from None
    kept = _read.get(source)
    if kept and kept[0] == stamp:
        return kept[1]
    body = source.read_text().removesuffix("\n")  # a file ends with a newline; the prompt doesn't
    _read[source] = (stamp, body)
    return body


def untrusted(source: str, text: str) -> str:
    """Text from outside the crew (a pull request, a commit, a page), marked so a bot reads it as data, never as
    instructions (crew-tools.md says so)."""
    return f'<untrusted source="{source}">\n{text.strip()}\n</untrusted>'
