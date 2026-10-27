"""The user's projects: the folders the crew works in, one Markdown file each in the crew's `projects/` folder.

    ---
    folder: ~/code/web
    ---
    The company website: Next.js, deployed from main by the user.

The name is the file's. The body says what the project is, in a line or two, for bots deciding which project a piece
of work belongs to; how to build and test it is the project's own business (its AGENTS.md, CLAUDE.md or README),
which the CLIs read when they work there.

A bot's chat isn't any one project's: it runs in the bot's own folder. Work on a project opens a thread or is
delegated in that project, and runs in its folder: in a worktree of its own when the bot writes and the folder is a
git repository, or in place otherwise, since the user gave the crew that folder by adding it. Only the user adds,
changes or removes a project.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

FOLDER = "projects"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    name: str
    folder: Path
    about: str  # what it is, for bots choosing where work goes
    path: Path  # its file

    @property
    def gist(self) -> str:
        first = next((line.strip() for line in self.about.splitlines() if line.strip()), "")
        return first if len(first) <= 200 else first[:199] + "…"


def load(home: Path) -> tuple[dict[str, Project], dict[str, str]]:
    """The crew's projects by name, and the files that couldn't be read, with why: one bad file doesn't lose the
    rest."""
    folder = home / FOLDER
    projects: dict[str, Project] = {}
    errors: dict[str, str] = {}
    for path in sorted(folder.glob("*.md")) if folder.is_dir() else []:
        try:
            project = parse(path.read_text(), path)
        except (ProjectError, yaml.YAMLError, OSError) as e:
            errors[path.name] = str(e)
            continue
        projects[project.name] = project
    return projects, errors


def parse(text: str, path: Path) -> Project:
    if not text.startswith("---"):
        raise ProjectError("a project starts with --- frontmatter naming its folder")
    try:
        raw, body = text[3:].split("\n---", 1)
    except ValueError:
        raise ProjectError("the frontmatter has no closing ---") from None
    meta = yaml.safe_load(raw) or {}
    if not isinstance(meta, dict) or not meta.get("folder"):
        raise ProjectError("the frontmatter needs `folder:`, the project's folder")
    name = path.stem
    if not NAME.fullmatch(name):
        raise ProjectError(f"{name!r} isn't a project name: lowercase letters, digits and dashes")
    return Project(name=name, folder=Path(str(meta["folder"])).expanduser(), about=body.strip(), path=path)


def name_for(folder: Path) -> str:
    """A name for a project in `folder`, from the folder's own name."""
    name = re.sub(r"[^a-z0-9]+", "-", folder.name.lower()).strip("-")[:40].strip("-")
    return name or "project"


def save(home: Path, name: str, folder: Path, about: str) -> Project:
    if not NAME.fullmatch(name):
        raise ProjectError(f"{name!r} isn't a project name: lowercase letters, digits and dashes, at most 40")
    folder = folder.expanduser()
    if not folder.is_dir():
        raise ProjectError(f"{folder} isn't a folder")
    crew = home.resolve()
    if folder.resolve() == crew or folder.resolve().is_relative_to(crew) or crew.is_relative_to(folder.resolve()):
        raise ProjectError(f"{folder} holds the crew's own settings, so it can't be a project")
    path = home / FOLDER / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    shown = str(folder).replace(str(Path.home()), "~", 1) if folder.is_relative_to(Path.home()) else str(folder)
    path.write_text(f"---\n{yaml.safe_dump({'folder': shown}, sort_keys=False)}---\n{about.strip()}\n")
    return parse(path.read_text(), path)


def delete(home: Path, name: str) -> None:
    path = home / FOLDER / f"{name}.md"
    if not path.is_file():
        raise ProjectError(f"no project named {name!r}")
    path.unlink()


def listing(projects: dict[str, Project]) -> str:
    """The projects as bots see them: name, folder and what it is."""
    return "\n".join(f"- {p.name} ({str(p.folder).replace(str(Path.home()), '~', 1)}){f': {p.gist}' if p.gist else ''}"
                     for p in projects.values())
