"""A crew is a folder: one role card per bot in `bots/`, and the shared brain every bot reads in `team/`.

A role card is Markdown with YAML frontmatter. The frontmatter is what the harness enforces (backend, access, MCP
servers); the body is the card itself (mission, owns, does not own, done means), given to the bot as instructions.
"""

import shutil
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from crewbots.backends import BACKENDS, Access, McpServer

DEFAULT_HOME = Path.home() / ".crewbots" / "crew"
CARD_FIELDS = {"name", "title", "description", "backend", "model", "access", "workspace", "worktree", "timeout", "mcp"}


@dataclass(frozen=True)
class Bot:
    name: str
    title: str
    description: str  # one line, shown to every other bot in the roster; it is how work gets routed
    instructions: str  # the role card body
    backend: str = "claude"
    model: str | None = None
    access: Access = Access.READ_ONLY
    workspace: Path | None = None  # where its tasks run unless a task says otherwise
    worktree: bool = True  # give each writing task its own git worktree when the workspace is a repo
    timeout_s: float | None = None
    mcp: dict[str, McpServer] = field(default_factory=dict)


@dataclass(frozen=True)
class Crew:
    home: Path
    bots: dict[str, Bot]
    team_files: dict[str, str]  # file name → contents, from team/

    def system_prompt(self, bot: Bot) -> str:
        roster = "\n".join(
            f"- {b.name} ({b.title}){' ← you' if b.name == bot.name else ''}: {b.description}" for b in self.bots.values()
        )
        parts = [
            f"You are {bot.name}, the {bot.title} of a crew of AI agents run by crewbots.",
            f"# Your role card\n\n{bot.instructions.strip()}",
            f"# The crew\n\n{roster}",
        ]
        parts += [f"# team/{name}\n\n{text.strip()}" for name, text in self.team_files.items()]
        return "\n\n".join(parts)


class CrewError(ValueError):
    pass


def load(home: Path) -> Crew:
    home = home.expanduser()
    bots_dir = home / "bots"
    if not bots_dir.is_dir():
        raise CrewError(f"no crew at {home}: expected a bots/ folder (run `crewbots init`)")
    bots: dict[str, Bot] = {}
    for path in sorted(bots_dir.glob("*.md")):
        bot = parse_card(path.read_text(), default_name=path.stem, source=path)
        if bot.name in bots:
            raise CrewError(f"{path}: a bot named {bot.name!r} already exists")
        bots[bot.name] = bot
    if not bots:
        raise CrewError(f"no role cards in {bots_dir}")
    team_dir = home / "team"
    team_files = {p.name: p.read_text() for p in sorted(team_dir.glob("*.md"))} if team_dir.is_dir() else {}
    return Crew(home=home, bots=bots, team_files=team_files)


def parse_card(text: str, default_name: str, source: Path | str = "<card>") -> Bot:
    meta, body = _frontmatter(text, source)
    unknown = set(meta) - CARD_FIELDS
    if unknown:
        raise CrewError(f"{source}: unknown fields {sorted(unknown)}; allowed: {sorted(CARD_FIELDS)}")
    name = str(meta.get("name", default_name))
    backend = meta.get("backend", "claude")
    if backend not in BACKENDS:
        raise CrewError(f"{source}: backend must be one of {sorted(BACKENDS)}, not {backend!r}")
    try:
        access = Access(meta.get("access", Access.READ_ONLY))
    except ValueError:
        raise CrewError(f"{source}: access must be one of {[a.value for a in Access]}") from None
    if "description" not in meta:
        raise CrewError(f"{source}: a description is required; it is how other bots know what this one does")
    workspace = meta.get("workspace")
    return Bot(
        name=name,
        title=str(meta.get("title", name)),
        description=str(meta["description"]),
        instructions=body,
        backend=backend,
        model=meta.get("model"),
        access=access,
        workspace=Path(workspace).expanduser() if workspace else None,
        worktree=bool(meta.get("worktree", True)),
        timeout_s=meta.get("timeout"),
        mcp=_mcp(meta.get("mcp") or {}, source),
    )


def init(home: Path) -> Path:
    """Writes the starter crew: a chief of staff, an engineer and a reviewer, and the team/ files."""
    home = home.expanduser()
    if (home / "bots").exists():
        raise CrewError(f"{home} already has a crew")
    template = resources.files("crewbots") / "templates" / "crew"
    with resources.as_file(template) as src:
        shutil.copytree(src, home, dirs_exist_ok=True)
    return home


def _frontmatter(text: str, source: Path | str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise CrewError(f"{source}: a role card starts with --- frontmatter")
    try:
        _, meta_text, body = text.split("---", 2)
    except ValueError:
        raise CrewError(f"{source}: the frontmatter has no closing ---") from None
    meta = yaml.safe_load(meta_text) or {}
    if not isinstance(meta, dict):
        raise CrewError(f"{source}: frontmatter must be a mapping")
    return meta, body.lstrip("\n")


def _mcp(raw: dict[str, Any], source: Path | str) -> dict[str, McpServer]:
    """The same shape as Claude's mcpServers config, so entries can be copied from it."""
    servers = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict) or "command" not in spec:
            raise CrewError(f"{source}: mcp server {name!r} needs a command")
        servers[name] = McpServer(
            command=spec["command"], args=[str(a) for a in spec.get("args", [])],
            env={k: str(v) for k, v in (spec.get("env") or {}).items()},
        )
    return servers
