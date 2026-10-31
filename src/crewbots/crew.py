"""A crew is a folder: one role card per bot in `bots/`, the shared brain every bot reads in `team/`, and the skills
bots can use in `skills/` (Agent Skills: a folder per skill with a SKILL.md, the format pstack and others ship in).

A role card is Markdown with YAML frontmatter. The frontmatter is what the harness enforces (backend, access, MCP
servers); the body is the card itself (mission, owns, does not own, done means), given to the bot as instructions.
"""

import json
import logging
import re
import shutil
from dataclasses import dataclass, field, replace
from datetime import timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from crewbots import frontmatter, paths, prompts, working_state
from crewbots import pages as pages_module
from crewbots import projects as projects_module
from crewbots.backends import BACKENDS, Access, McpServer
from crewbots.memory import CREW_SCOPE, Memory
from crewbots.projects import Project

CARD_FIELDS = {
    "name", "title", "description", "backend", "model", "access", "workspace", "worktree", "timeout", "mcp",
    "can_delegate", "skills", "budget", "network", "display_name", "avatar", "effort", "context_window", "proactive",
}
# How much conversation a bot's session holds before the CLI compacts it, and what the rollover is measured against.
# Unless its card says, the model's whole window: on a 1M model, the rollover comes at about 700K tokens.
DEFAULT_CONTEXT_WINDOW = "auto"
CONTEXT_WINDOW_RANGE = (100_000, 1_000_000)  # what Claude Code's --autocompact takes
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")  # what each CLI accepts is narrower; its model list says
# A bot's face in the app: one of these colors and shapes, or one picked from its name.
AVATAR_COLORS = ("blue", "orange", "aqua", "yellow", "magenta", "green", "violet", "red")  # validated for charts too
AVATAR_SHAPES = ("circle", "drop", "square", "flower", "hex")
BUDGET_WINDOWS = {"day": timedelta(days=1), "week": timedelta(days=7)}

NO_REPLY = "NO_REPLY"  # a bot's whole reply in a group chat when it has nothing to add


def is_no_reply(text: str) -> bool:
    """Whether a bot's reply says it has nothing to add: its first non-empty line is NO_REPLY, in any case, bare or in
    light formatting. Anything after that line (an explanation) is dropped with it."""
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    return first.strip().strip("`*_. ").upper() == NO_REPLY


@dataclass(frozen=True)
class Budget:
    """Tokens a bot may use over a rolling window, e.g. 2,000,000 a week. Once they're used, its next turns wait."""

    tokens: int
    per: str  # day or week

    @property
    def window(self) -> timedelta:
        return BUDGET_WINDOWS[self.per]


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
    mcp: dict[str, McpServer] = field(default_factory=dict)  # once loaded: the crew's servers it names, and its own
    mcp_names: tuple[str, ...] = ()  # the crew's servers (mcp.json) its role card names
    can_delegate: bool = False  # legacy coordinator flag, used once to initialize crew settings
    skills: tuple[str, ...] = ()  # (): every skill but the synced ones; ("all",): every one; ("none",); or just these
    budget: Budget | None = None
    network: bool | tuple[str, ...] = False  # web search and pages, and a writer's commands: none, any, or these hosts
    path: Path | None = None  # its role card
    display_name: str | None = None  # what you call it, e.g. Roger; `name` stays its handle, for @mentions and tasks
    avatar: dict[str, str] | None = None  # {color, shape}, from AVATAR_COLORS and AVATAR_SHAPES
    effort: str | None = None  # reasoning effort for every turn it takes, its threads' too; None: the CLI's default
    context_window: int | None = None  # tokens its sessions hold before compacting; None: the model's whole window
    proactive: bool = True  # whether a check-in of its may start low-risk work on its own

    @property
    def label(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path  # the SKILL.md; bots read it when a task matches
    source: str = "crew"  # the crew's skills/ folder, or the global folder it came from
    synced: bool = False  # synced to Claude Code from the user's claude.ai account (documents, slides, briefs)

    @property
    def gist(self) -> str:
        """What it's for, short enough to list every skill in every bot's instructions: its description's first
        sentence, the rest being in its SKILL.md."""
        first = re.split(r"(?<=[.!?])\s", self.description, maxsplit=1)[0]
        return first if len(first) <= 200 else first[:199].rsplit(" ", 1)[0] + "…"


MCP_FILE = "mcp.json"  # the crew's MCP servers, in Claude's mcpServers shape
MCP_NAME = re.compile(r"[A-Za-z0-9_-]+")  # a server's name becomes a config key in Codex



@dataclass(frozen=True)
class Crew:
    home: Path
    bots: dict[str, Bot]
    team_files: dict[str, str]  # file name → contents, from team/
    skills: dict[str, Skill] = field(default_factory=dict)
    global_skill_dirs: tuple[Path, ...] = ()
    mcp_servers: dict[str, McpServer] = field(default_factory=dict)  # the crew's own list (mcp.json)
    shadowed_skills: tuple[Skill, ...] = ()  # skills named like one found first, so not used
    projects: dict[str, Project] = field(default_factory=dict)  # the user's, from projects/
    project_errors: dict[str, str] = field(default_factory=dict)  # project files that couldn't be read, and why

    def project(self, name: str) -> Project:
        if name not in self.projects:
            known = ", ".join(sorted(self.projects)) or "none yet: the user adds them in the app"
            raise CrewError(f"no project named {name!r}; the projects are {known}")
        return self.projects[name]

    def project_at(self, folder: Path) -> Project | None:
        """The project whose folder this is, if any."""
        folder = folder.expanduser().resolve()
        return next((p for p in self.projects.values() if p.folder.resolve() == folder), None)

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    def skills_for(self, bot: Bot) -> list[Skill]:
        """A bot's skills: the ones its card names, or else every one but those synced from claude.ai, which are for
        the user's own documents and briefs and only reach a bot whose card names them."""
        if "all" in bot.skills:
            return list(self.skills.values())
        if not bot.skills:
            return [s for s in self.skills.values() if not s.synced]
        return [self.skills[name] for name in bot.skills if name != "none"]

    @property
    def skill_dirs(self) -> list[Path]:
        """Where the skills bots can use live, for backends that need read access granted to a folder."""
        return [d for d in (self.skills_dir, *self.global_skill_dirs) if d.is_dir()]

    @property
    def memory(self) -> Memory:
        return Memory(self.home)

    def folder_of(self, bot: Bot) -> Path:
        """The bot's own folder: where its chat, and work that isn't any project's, runs."""
        return bot.workspace or paths.bot_folders() / bot.name

    def pages_of(self, bot: Bot) -> Path:
        """Where the bot's pages are, its scratchpad first: its own folder in Documents, wherever its chat runs."""
        return paths.bot_folders() / bot.name

    @property
    def routines_dir(self) -> Path:
        return self.home / "routines"

    def living_parts(self, bot: Bot) -> dict[str, tuple[str, str]]:
        """The parts of a bot's instructions that change while its sessions run, by key: (heading, text). A session
        keeps the instructions it started with, so what changes in these reaches it in the turn instead (the runner's
        `<crew-updates>`)."""
        roster = "\n".join(
            f"- {b.label} (@{b.name}, {b.title}){' ← you' if b.name == bot.name else ''}: {b.description}"
            for b in self.bots.values()
        )
        parts = {"role": ("Your role card", bot.instructions.strip()), "crew": ("The crew", roster),
                 "projects": ("Projects", projects_module.listing(self.projects) or "None yet.")}
        if skills := self.skills_for(bot):
            parts["skills"] = ("Skills", "\n".join(f"- {s.name}: {s.gist} ({s.path})" for s in skills))
        parts |= {f"team/{name}": (f"team/{name}", text.strip()) for name, text in self.team_files.items()}
        memory = self.memory
        if core := [f"## {whose}\n\n{text}" for whose, text in (("Yours", memory.core(bot.name)),
                                                                 ("The crew's", memory.core(CREW_SCOPE))) if text]:
            parts["core"] = ("Core memory", "\n\n".join(core))
        parts["notes"] = ("Notes", memory.prompt(bot.name) or "No notes yet.")
        logs = [(whose, memory.recent(scope)) for whose, scope in (("Yours", bot.name), ("The crew's", CREW_SCOPE))]
        parts["lately"] = ("Lately", "\n\n".join(f"## {whose}\n\n{text}" for whose, text in logs if text) or "No recent logs.")
        try:
            parts["working-state"] = ("Working state", working_state.prompt_data(
                working_state.read(working_state.path(memory.root, bot.name))))
        except Exception:
            logging.getLogger(__name__).exception("Could not read working state for %s", bot.name)
        parts["scratchpad"] = ("Your scratchpad", pages_module.for_instructions(self.pages_of(bot)))
        return parts

    def system_prompt(self, bot: Bot, delegated_by: str | None = None, reviewing: str | None = None,
                      briefing: str | None = None, routine: str | None = None, setting: str | None = None,
                      recent: str | None = None, state: str | None = None, coordinator: bool = False, chief: bool = False) -> str:
        living = self.living_parts(bot)

        def section(key: str, *intro: str) -> str:
            heading, text = living[key]
            return "\n\n".join((f"# {heading}", *intro, text))

        # What stays the same for every task of this bot comes first and what changes comes last: the model's prompt
        # cache matches from the start, so a bot's sessions share the cached start, and a new note or a task's own
        # setting only misses from there on.
        parts = [
            prompts.text("system/identity", self.home, label=bot.label, title=bot.title, handle=bot.name),
            section("role"),
            section("crew"),
            section("projects", prompts.text("system/projects", self.home, folder=paths.shown(self.folder_of(bot)))),
            prompts.text("crew-tools", self.home) + "\n\n" + prompts.text("delegation", self.home)
            + ("\n\n" + prompts.text("coordinating", self.home) if coordinator else "")
            + ("\n\n" + prompts.text("chief-push", self.home) if chief else ""),
        ]
        if "skills" in living:
            parts.append(section("skills", prompts.text("system/skills", self.home)))
        parts += [section(key) for key in living if key.startswith("team/")]
        if "core" in living:
            parts.append(section("core", prompts.text("system/core", self.home)))
        if living["lately"][1] != "No recent logs.":
            parts.append(section("lately"))
        if "working-state" in living:
            parts.append(section("working-state"))
        parts.append(prompts.text("memory-guide", self.home) + f"\n\n{living['notes'][1]}")
        parts.append(prompts.text("scratchpad-guide", self.home, folder=paths.shown(self.pages_of(bot)))
                     + f"\n\n## Scratchpad\n\n{living['scratchpad'][1]}")
        if setting:
            task = setting
        elif delegated_by:
            task = prompts.text("this-task/delegated", self.home, delegated_by=delegated_by)
        elif routine:
            task = prompts.text("this-task/routine", self.home, routine=repr(routine))
        else:
            task = prompts.text("this-task/user", self.home)
        parts.append(f"# This task\n\n{task}")
        if reviewing:
            parts.append(f"# This review\n\n{reviewing}")
        if state:
            parts.append(f"# Where this task stands\n\nThe state you keep for it with `update_state`:\n\n{state}")
        if briefing:  # a fresh session's start: a briefing the bot wrote, or the conversation's last messages
            parts.append(f"# Where things stand\n\n{briefing}")
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
        bot = replace(parse_card(path.read_text(), default_name=path.stem, source=path), path=path)
        if bot.name in bots:
            raise CrewError(f"{path}: a bot named {bot.name!r} already exists")
        bots[bot.name] = bot
    if not bots:
        raise CrewError(f"no role cards in {bots_dir}")
    team_dir = home / "team"
    team_files = {p.name: p.read_text() for p in sorted(team_dir.glob("*.md"))} if team_dir.is_dir() else {}
    skills = load_skills(home / "skills")
    global_dirs = paths.global_skill_dirs()  # the crew's own come first; a global one of the same name is left out
    shadowed: list[Skill] = []  # the same name in a later folder: listed, but the first one is the one used
    for folder in global_dirs:
        for name, skill in load_global_skills(folder).items():
            if name in skills:
                shadowed.append(skill)
            else:
                skills[name] = skill
    for bot in bots.values():
        if missing := [s for s in bot.skills if s not in ("all", "none") and s not in skills]:
            raise CrewError(f"{bot.name}: no skills named {missing} in {home / 'skills'} or your global skills")
    servers = load_mcp(home)
    for name, bot in list(bots.items()):
        if missing := [n for n in bot.mcp_names if n not in servers]:
            raise CrewError(f"{bot.name}: no MCP servers named {missing} in the crew's {MCP_FILE}")
        named = {n: servers[n] for n in bot.mcp_names}
        if bot.backend.startswith("codex") and (sse := [n for n, s in {**named, **bot.mcp}.items() if s.transport == "sse"]):
            raise CrewError(f"{bot.name}: Codex can't use SSE MCP servers ({', '.join(sse)}); run it on Claude, or "
                            "use the server's streamable HTTP address")
        bots[name] = replace(bot, mcp={**named, **bot.mcp})
    projects, project_errors = projects_module.load(home)
    return Crew(home=home, bots=bots, team_files=team_files, skills=skills, global_skill_dirs=global_dirs,
                mcp_servers=servers, shadowed_skills=tuple(shadowed), projects=projects, project_errors=project_errors)


def stamp(home: Path) -> tuple[tuple[str, int], ...]:
    """What the crew is made of on disk, as names and modification times: when it changes, the crew is read again."""
    home = home.expanduser()
    files = [*home.glob("bots/*.md"), *home.glob("team/*.md"), *home.glob("skills/**/SKILL.md"), *home.glob(MCP_FILE),
             *home.glob(f"{projects_module.FOLDER}/*.md"),
             *(p for d in paths.global_skill_dirs() if d.is_dir() for p in d.rglob("SKILL.md"))]
    found = []
    for p in sorted(files):
        try:
            found.append((str(p.relative_to(home) if p.is_relative_to(home) else p), p.stat().st_mtime_ns))
        except OSError:
            continue  # deleted while looking
    return tuple(found)


def load_skills(skills_dir: Path) -> dict[str, Skill]:
    """`skills/<name>/SKILL.md` is the skill `name`. Anything deeper is a pack, dropped in as it ships, and is
    namespaced like a Claude Code plugin: `skills/pstack/.../arena/SKILL.md` is `pstack:arena`."""
    skills: dict[str, Skill] = {}
    if not skills_dir.is_dir():
        return skills
    for path in sorted(skills_dir.rglob("SKILL.md")):
        meta, _ = _frontmatter(path.read_text(), path)
        base = str(meta.get("name", path.parent.name))
        parts = path.relative_to(skills_dir).parts
        name = base if len(parts) == 2 else f"{parts[0]}:{base}"
        if name in skills:
            raise CrewError(f"{path}: a skill named {name!r} already exists at {skills[name].path}")
        if "description" not in meta:
            raise CrewError(f"{path}: a skill needs a description; it is how a bot knows when to use it")
        skills[name] = Skill(name=name, description=" ".join(str(meta["description"]).split()), path=path)
    return skills


def load_global_skills(folder: Path) -> dict[str, Skill]:
    """Skills in one of your global folders, named as each SKILL.md names itself. Not the crew's to vouch for, so one
    that can't be read is left out rather than stopping the crew."""
    skills: dict[str, Skill] = {}
    if not folder.is_dir():
        return skills
    for path in sorted(folder.rglob("SKILL.md")):
        if any(part.startswith(".") for part in path.relative_to(folder).parts):
            continue  # a CLI's own built-in ones (Codex keeps them in .system), which it gives itself
        try:
            meta, _ = _frontmatter(path.read_text(), path)
        except (CrewError, OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        name = str(meta.get("name") or path.parent.name)
        if meta.get("description") and name not in skills:
            skills[name] = Skill(name=name, description=" ".join(str(meta["description"]).split()), path=path,
                                 source=paths.shown(folder),
                                 synced=path.relative_to(folder).parts[0] == "synced")
    return skills


def add_skills(home: Path, source: Path, name: str | None = None) -> list[str]:
    """Copies a skill folder, or a whole pack (a folder with skills somewhere inside, kept as it is so its relative
    links still work), into the crew. Returns the names of the skills it added."""
    source = source.expanduser().resolve()
    if not any(source.rglob("SKILL.md")):
        raise CrewError(f"no SKILL.md in {source}")
    target = home.expanduser() / "skills" / (name or source.name)
    if target.exists():
        raise CrewError(f"{target} already exists; pass another name")
    before = set(load_skills(home.expanduser() / "skills"))
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git"))
    try:
        after = set(load_skills(home.expanduser() / "skills"))
    except CrewError:
        shutil.rmtree(target)  # only ever the copy made just above
        raise
    return sorted(after - before)


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
        mcp=_mcp(meta.get("mcp") if isinstance(meta.get("mcp"), dict) else {}, source),
        mcp_names=_mcp_names(meta.get("mcp"), source),
        can_delegate=bool(meta.get("can_delegate", False)),
        proactive=bool(meta.get("proactive", True)) and backend != "antigravity",
        skills=_skill_names(meta.get("skills"), source),
        budget=_budget(meta.get("budget"), source),
        network=_network(meta.get("network", False), backend, source),
        display_name=str(meta["display_name"]).strip() or None if meta.get("display_name") else None,
        avatar=_avatar(meta.get("avatar"), source),
        effort=_effort(meta.get("effort"), source),
        context_window=_context_window(meta.get("context_window", DEFAULT_CONTEXT_WINDOW), source),
    )


def init(home: Path) -> Path:
    """Writes the starter crew: a chief of staff, an engineering lead with four engineers, a reviewer and a critic, the
    team/ files, the routines, and the Engineering group chat (rooms/)."""
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
    if (parts := frontmatter.split(text)) is None:
        raise CrewError(f"{source}: the frontmatter has no closing ---")
    meta_text, body = parts
    meta = yaml.safe_load(meta_text) or {}
    if not isinstance(meta, dict):
        raise CrewError(f"{source}: frontmatter must be a mapping")
    return meta, body.lstrip("\n")


def _effort(raw: Any, source: Path | str) -> str | None:
    if raw is None or raw == "":
        return None
    if raw not in EFFORTS:
        raise CrewError(f"{source}: effort is one of {list(EFFORTS)}, not {raw!r}")
    return str(raw)


def _context_window(raw: Any, source: Path | str) -> int | None:
    """200000, "200k" or "1m"; "auto" leaves it to the model's whole window."""
    if raw == "auto":
        return None
    text = str(raw).strip().lower().replace("_", "").replace(",", "")
    scale = {"k": 1_000, "m": 1_000_000}.get(text[-1:], 1)
    try:
        tokens = int(float(text[:-1] if scale > 1 else text) * scale)
    except ValueError:
        tokens = 0
    low, high = CONTEXT_WINDOW_RANGE
    if not low <= tokens <= high:
        raise CrewError(f"{source}: context_window is a number of tokens from 100k to 1m (such as 200k), or auto")
    return tokens


def _avatar(raw: Any, source: Path | str) -> dict[str, str] | None:
    if raw is None:
        return None
    if (not isinstance(raw, dict) or raw.get("color", AVATAR_COLORS[0]) not in AVATAR_COLORS
            or raw.get("shape", AVATAR_SHAPES[0]) not in AVATAR_SHAPES):
        raise CrewError(f"{source}: avatar is {{color: one of {list(AVATAR_COLORS)}, shape: one of {list(AVATAR_SHAPES)}}}")
    return {k: str(raw[k]) for k in ("color", "shape") if k in raw}


def _network(raw: Any, backend: str, source: Path | str) -> bool | tuple[str, ...]:
    if raw is False or raw is None or raw == []:
        return False
    if raw is True:
        return True
    if not isinstance(raw, list) or not all(isinstance(h, str) and h.strip() for h in raw):
        raise CrewError(f"{source}: network is true, or a list of hosts such as [pypi.org, \"*.npmjs.org\"]")
    if backend.startswith("codex") or backend == "antigravity":
        label = "Antigravity" if backend == "antigravity" else "Codex"
        raise CrewError(f"{source}: {label}'s sandbox can't keep to a list of hosts; give this bot network: true, or run "
                        "it on Claude")
    return tuple(h.strip() for h in raw)


def _budget(raw: Any, source: Path | str) -> Budget | None:
    if raw is None:
        return None
    tokens = raw.get("tokens") if isinstance(raw, dict) else None
    per = raw.get("per", "week") if isinstance(raw, dict) else None
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens <= 0 or per not in BUDGET_WINDOWS:
        raise CrewError(f"{source}: budget is {{tokens: <number>, per: day or week}}")
    return Budget(tokens=tokens, per=per)


def _skill_names(raw: Any, source: Path | str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if raw in ("all", "none"):
        return (raw,)
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise CrewError(f"{source}: skills must be a list of skill names, all, or none")
    return tuple(raw)


def _mcp(raw: dict[str, Any], source: Path | str) -> dict[str, McpServer]:
    """The same shape as Claude's mcpServers config, so entries can be copied from it."""
    return {name: mcp_server(name, spec, source) for name, spec in raw.items()}


def mcp_server(name: str, spec: Any, source: Path | str) -> McpServer:
    """One server, as Claude's config writes it: a `command` (with `args` and `env`) it starts, or a `url` (with
    `headers`), `type: http` unless it says `sse`."""
    if name == "crewbots":
        raise CrewError(f"{source}: the mcp server name crewbots is reserved for the crew's own tools")
    if not MCP_NAME.fullmatch(name):
        raise CrewError(f"{source}: mcp server name {name!r} must be letters, digits, - or _")
    if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
        raise CrewError(f"{source}: mcp server {name!r} needs a command, or a url")
    if spec.get("one_at_a_time") and not (spec.get("url") and spec.get("type", "http") == "http"):
        raise CrewError(f"{source}: mcp server {name!r} can be one_at_a_time only as an http server, at a url")
    if spec.get("url"):
        transport = "sse" if spec.get("type") == "sse" else "http"
        return McpServer(url=str(spec["url"]), headers={k: str(v) for k, v in (spec.get("headers") or {}).items()},
                         transport=transport, one_at_a_time=bool(spec.get("one_at_a_time")))
    return McpServer(command=str(spec["command"]), args=[str(a) for a in spec.get("args") or []],
                     env={k: str(v) for k, v in (spec.get("env") or {}).items()})


def _mcp_names(raw: Any, source: Path | str) -> tuple[str, ...]:
    """A role card's `mcp` as a list: the names of the crew's servers (mcp.json) the bot may use."""
    if raw is None or isinstance(raw, dict):
        return ()
    if not isinstance(raw, list) or not all(isinstance(n, str) for n in raw):
        raise CrewError(f"{source}: mcp is a list of the crew's MCP server names, or servers of its own")
    return tuple(raw)


def load_mcp(home: Path) -> dict[str, McpServer]:
    """The crew's MCP servers, `mcp.json` in Claude's `{"mcpServers": {...}}` shape: ones you add in the app or copy
    from your other apps, for role cards to name."""
    path = home / MCP_FILE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text()).get("mcpServers") or {}
    except (ValueError, AttributeError) as e:
        raise CrewError(f"{path}: not readable as JSON with an mcpServers object ({e})") from None
    return {name: mcp_server(name, spec, path) for name, spec in raw.items()}
