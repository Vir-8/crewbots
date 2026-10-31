"""The user's projects: the folders the crew works in, one Markdown file each in the crew's `projects/` folder.

    ---
    folder: ~/code/web
    ---
    The company website: Next.js, deployed from main by the user.

The name is the file's. The body says what the project is, in a line or two, for bots deciding which project a piece
of work belongs to; how to build and test it is the project's own business (its AGENTS.md, CLAUDE.md or README),
which the CLIs read when they work there.

A bot's chat isn't any one project's: it runs in the bot's own folder. Work on a project opens a thread or is
delegated in that project, and runs in its folder, as the project's `isolation` says: `worktree`, a worktree of its own
when the bot writes and the folder is a git repository (the default), or `in_place`, the folder itself, on whatever
branch it's on, beside the user and the other bots (a Unity project's default: one Editor can have it open, and
engineers split the work by files). A folder that isn't a git repository is worked in place. Only the user adds,
changes or removes a project. Adding a Unity project gives the crew the `verify-unity` skill.
"""

import re
import shutil
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

import yaml

from crewbots import frontmatter, paths

FOLDER = "projects"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
Isolation = Literal["in_place", "worktree"]
ISOLATIONS = ("in_place", "worktree")


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class Project:
    name: str
    folder: Path
    about: str  # what it is, for bots choosing where work goes
    path: Path  # its file
    isolation_setting: Isolation | None = None
    ci: tuple[str, ...] = ()  # commands to check before a push, for a repo with no GitHub workflow

    @property
    def isolation(self) -> Isolation:
        if self.isolation_setting:
            return self.isolation_setting
        return "in_place" if is_unity(self.folder) else "worktree"

    @property
    def gist(self) -> str:
        first = next((line.strip() for line in self.about.splitlines() if line.strip()), "")
        return first if len(first) <= 200 else first[:199] + "…"


def is_unity(folder: Path) -> bool:
    return (folder / "ProjectSettings" / "ProjectVersion.txt").is_file()


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
    if (parts := frontmatter.split(text)) is None:
        raise ProjectError("the frontmatter has no closing ---")
    raw, body = parts
    meta = yaml.safe_load(raw) or {}
    if not isinstance(meta, dict) or not meta.get("folder"):
        raise ProjectError("the frontmatter needs `folder:`, the project's folder")
    name = path.stem
    if not NAME.fullmatch(name):
        raise ProjectError(f"{name!r} isn't a project name: lowercase letters, digits and dashes")
    isolation = meta.get("isolation")
    if "isolation" in meta and isolation not in ISOLATIONS:
        raise ProjectError("isolation must be `in_place` or `worktree`")
    ci = meta.get("ci", [])
    if not isinstance(ci, list) or not all(isinstance(c, str) and c.strip() for c in ci):
        raise ProjectError("ci must be a list of commands")
    return Project(name=name, folder=Path(str(meta["folder"])).expanduser(), about=body.strip(), path=path,
                   isolation_setting=isolation, ci=tuple(ci))


def name_for(folder: Path) -> str:
    """A name for a project in `folder`, from the folder's own name."""
    name = re.sub(r"[^a-z0-9]+", "-", folder.name.lower()).strip("-")[:40].strip("-")
    return name or "project"


def save(home: Path, name: str, folder: Path, about: str, isolation: Isolation | None = None) -> Project:
    if isolation is not None and isolation not in ISOLATIONS:
        raise ProjectError("isolation must be `in_place` or `worktree`")
    if not NAME.fullmatch(name):
        raise ProjectError(f"{name!r} isn't a project name: lowercase letters, digits and dashes, at most 40")
    folder = folder.expanduser()
    if not folder.is_dir():
        raise ProjectError(f"{folder} isn't a folder")
    crew = home.resolve()
    if folder.resolve() == crew or folder.resolve().is_relative_to(crew) or crew.is_relative_to(folder.resolve()):
        raise ProjectError(f"{folder} holds the crew's own settings, so it can't be a project")
    path = home / FOLDER / f"{name}.md"
    old = parse(path.read_text(), path) if path.is_file() else None
    if isolation is None and old:
        isolation = old.isolation_setting
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"folder": paths.shown(folder), **({"isolation": isolation} if isolation is not None else {}),
            **({"ci": list(old.ci)} if old and old.ci else {})}
    path.write_text(f"---\n{yaml.safe_dump(meta, sort_keys=False)}---\n{about.strip()}\n")
    if is_unity(folder) and (old is None or old.folder.resolve() != folder.resolve()):
        _add_unity_skill(home)
    return parse(path.read_text(), path)


def _add_unity_skill(home: Path) -> None:
    """Gives the crew the skill for verifying Unity work, unless it has it already. It comes with a Unity project, so
    a crew without one never sees it."""
    target = home / "skills" / "verify-unity"
    if target.exists():
        return
    skill = resources.files("crewbots") / "templates" / "integrations" / "unity" / "skills" / "verify-unity"
    with resources.as_file(skill) as source:
        shutil.copytree(source, target)


def delete(home: Path, name: str) -> None:
    path = home / FOLDER / f"{name}.md"
    if not path.is_file():
        raise ProjectError(f"no project named {name!r}")
    path.unlink()


def listing(projects: dict[str, Project]) -> str:
    """The projects as bots see them: name, folder, how its work is done, and what it is."""
    how = {"in_place": "worked in its folder itself, on its current branch: no worktrees or branches",
           "worktree": "each task in a worktree of its own"}
    return "\n".join(f"- {p.name} ({paths.shown(p.folder)}; {how[p.isolation]})"
                     + (f": {p.gist}" if p.gist else "") for p in projects.values())
