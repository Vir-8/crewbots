"""A crew is a folder: one role card per bot in `bots/`, the shared brain every bot reads in `team/`, and the skills
bots can use in `skills/` (Agent Skills: a folder per skill with a SKILL.md, the format pstack and others ship in).

A role card is Markdown with YAML frontmatter. The frontmatter is what the harness enforces (backend, access, MCP
servers); the body is the card itself (mission, owns, does not own, done means), given to the bot as instructions.
"""

import json
import re
import shutil
from dataclasses import dataclass, field, replace
from datetime import timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from crewbots import paths
from crewbots.backends import BACKENDS, Access, McpServer
from crewbots.memory import MEMORY_GUIDE, Memory

CARD_FIELDS = {
    "name", "title", "description", "backend", "model", "access", "workspace", "worktree", "timeout", "mcp",
    "can_delegate", "skills", "budget", "network", "display_name", "avatar", "effort", "context_window",
}
# How much conversation a bot's session holds before the CLI compacts it, and what the rollover is measured against.
# Without it, the model's whole window counts: on a 1M model the rollover would wait for 700K tokens.
DEFAULT_CONTEXT_WINDOW = 200_000
CONTEXT_WINDOW_RANGE = (100_000, 1_000_000)  # what Claude Code's --autocompact takes
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")  # what each CLI accepts is narrower; its model list says
# A bot's face in the app: one of these colors and shapes, or one picked from its name.
AVATAR_COLORS = ("blue", "orange", "aqua", "yellow", "magenta", "green", "violet", "red")  # validated for charts too
AVATAR_SHAPES = ("circle", "drop", "square", "flower", "hex")
BUDGET_WINDOWS = {"day": timedelta(days=1), "week": timedelta(days=7)}

NO_REPLY = "NO_REPLY"  # a bot's whole reply in a group chat when it has nothing to add
CHAT_SETTING = (
    "This is your ongoing chat with the user. It doesn't end: it's the same conversation tomorrow. Answer here, and "
    "keep it conversational. For real work, open a thread with start_thread; its result comes back here. Messages "
    "that start with [routine: name] come from the crew's routines, not from the user typing."
)
SEAT_SETTING = (
    "This is the group chat {title!r}, with the user and {others}. {lead} New messages reach you as [name]: text. "
    "Your final message is posted to the room as yours, so write it to the room. If you have nothing to add, reply "
    "with exactly " + NO_REPLY + ". @mention a teammate by name to bring them in."
)

CREW_TOOLS = """# Working in the crew

You have crewbots tools, from the MCP server named crewbots:

- `handoff(done, output, open, needs)`: call it once when you finish, as the HANDOFF in team/protocol.md. `output` lists
  file paths, branches or links. Then end your reply with a short summary.
- `escalate(question)`: when you need a decision that is not yours to make (team/rules.md). Ask one clear question,
  then end your turn. The answer arrives as your next message.
- `review(verdict, summary, findings)`: only when your task is a review. Your verdict is what counts, not your prose.
- `start_thread(title, goal)`: open a thread for a piece of real work (several steps, files, anything that takes a
  while), so the conversation you're in stays a conversation. You work the thread in parallel; when it's done, its
  result comes back here. Write the goal so it stands alone.
- `post(text, room?)`: say something in a group chat, by default the crew's own. @mention a teammate by name to bring
  them in; that's how you ask another bot for something."""

DELEGATION_TOOLS = """You can delegate. Only you assign work, and every task has exactly one owner:

- `delegate(bot, goal, workspace?, review_of?)`: give one task to one bot. Write the goal so it stands alone: the
  outcome, the context the bot needs, and what done means. To have work reviewed, pass `review_of` with the task id of
  the work, so the reviewer reads that task's worktree.
- `tasks()`: the tasks you delegated, with their status, handoffs and results. For looking something up, never for
  waiting: results come to you.
- `message(task_id, text)`: answer a task's escalation, or send it review objections to fix.
- `add_bot(name, title, description, instructions, ...)`: when the user asks for a new bot, add it, with a role card
  you write. It gets all the crew's MCP servers unless you name some, and network only if you have it. You can't
  remove or change bots.
- `propose_merge(task_id, summary)`: put approved work in front of the user to merge. You never merge; the user does.
  Uncommitted changes in the worktree are expected: crewbots commits them when the user merges.

Reviews are counted in code. A round is one reviewed version of the work. After three rounds that still fail, crewbots
stops further review rounds and revisions, and you report to the user instead. Work can be proposed for merging only
when every review of its current version passed.

Delegated tasks run in parallel, so delegate independent work together. Do not wait or poll after delegating: end your
turn. When all of your running tasks have finished, or one escalates, their results arrive as your next message."""


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
    can_delegate: bool = False  # only coordinators assign work; the harness refuses delegation from anyone else
    skills: tuple[str, ...] = ()  # () or ("all",): every skill, the crew's and global; ("none",); or just these
    budget: Budget | None = None
    network: bool | tuple[str, ...] = False  # web search and pages, and a writer's commands: none, any, or these hosts
    path: Path | None = None  # its role card
    display_name: str | None = None  # what you call it, e.g. Roger; `name` stays its handle, for @mentions and tasks
    avatar: dict[str, str] | None = None  # {color, shape}, from AVATAR_COLORS and AVATAR_SHAPES
    effort: str | None = None  # reasoning effort for every turn it takes, its threads' too; None: the CLI's default
    context_window: int | None = None  # tokens its sessions hold before compacting; None: the model's whole window

    @property
    def label(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path  # the SKILL.md; bots read it when a task matches
    source: str = "crew"  # the crew's skills/ folder, or the global folder it came from


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

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    def skills_for(self, bot: Bot) -> list[Skill]:
        if not bot.skills or "all" in bot.skills:
            return list(self.skills.values())
        return [self.skills[name] for name in bot.skills if name != "none"]

    @property
    def skill_dirs(self) -> list[Path]:
        """Where the skills bots can use live, for backends that need read access granted to a folder."""
        return [d for d in (self.skills_dir, *self.global_skill_dirs) if d.is_dir()]

    @property
    def memory(self) -> Memory:
        return Memory(self.home)

    @property
    def routines_dir(self) -> Path:
        return self.home / "routines"

    def system_prompt(self, bot: Bot, delegated_by: str | None = None, reviewing: str | None = None,
                      briefing: str | None = None, routine: str | None = None, setting: str | None = None) -> str:
        roster = "\n".join(
            f"- {b.label} (@{b.name}, {b.title}){' ← you' if b.name == bot.name else ''}: {b.description}"
            for b in self.bots.values()
        )
        # What stays the same for every task of this bot comes first and what changes comes last: the model's prompt
        # cache matches from the start, so a bot's sessions share the cached start, and a new note or a task's own
        # setting only misses from there on.
        parts = [
            f"You are {bot.label}, the {bot.title} of a crew of AI agents run by crewbots. Your handle is @{bot.name}.",
            f"# Your role card\n\n{bot.instructions.strip()}",
            f"# The crew\n\n{roster}",
            CREW_TOOLS + ("\n\n" + DELEGATION_TOOLS if bot.can_delegate else ""),
        ]
        if skills := self.skills_for(bot):
            listing = "\n".join(f"- {s.name}: {s.description} ({s.path})" for s in skills)
            parts.append(
                "# Skills\n\nSkills are tested procedures. When a task matches one, read its SKILL.md first and follow "
                f"it. Where a skill says to spawn subagents, you may use your own; across bots, delegate.\n\n{listing}"
            )
        parts += [f"# team/{name}\n\n{text.strip()}" for name, text in self.team_files.items()]
        notes = self.memory.prompt(bot.name)
        parts.append(MEMORY_GUIDE + (f"\n\n{notes}" if notes else "\n\nNo notes yet."))
        parts.append("# This task\n\n" + (
            setting if setting else
            f"This task was delegated to you by {delegated_by}; your handoff goes back to them."
            if delegated_by else
            f"The crew's routine {routine!r} started this task, not a message from the user. Your final message is what "
            "the user reads, when they next look; keep it short, and lead with anything that needs them."
            if routine else "This task comes from the user; your final message is what they read."))
        if reviewing:
            parts.append(f"# This review\n\n{reviewing}")
        if briefing:
            parts.append("# Where things stand\n\nThis task's conversation grew long, so it continues in a fresh session. "
                         f"This is the briefing you wrote for it:\n\n{briefing}")
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
    return Crew(home=home, bots=bots, team_files=team_files, skills=skills, global_skill_dirs=global_dirs,
                mcp_servers=servers, shadowed_skills=tuple(shadowed))


def stamp(home: Path) -> tuple[tuple[str, int], ...]:
    """What the crew is made of on disk, as names and modification times: when it changes, the crew is read again."""
    home = home.expanduser()
    files = [*home.glob("bots/*.md"), *home.glob("team/*.md"), *home.glob("skills/**/SKILL.md"), *home.glob(MCP_FILE),
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
                                 source=str(folder).replace(str(Path.home()), "~"))
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
        skills=_skill_names(meta.get("skills"), source),
        budget=_budget(meta.get("budget"), source),
        network=_network(meta.get("network", False), access, backend, source),
        display_name=str(meta["display_name"]).strip() or None if meta.get("display_name") else None,
        avatar=_avatar(meta.get("avatar"), source),
        effort=_effort(meta.get("effort"), source),
        context_window=_context_window(meta.get("context_window", DEFAULT_CONTEXT_WINDOW), source),
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


def _network(raw: Any, access: Access, backend: str, source: Path | str) -> bool | tuple[str, ...]:
    if raw is False or raw is None or raw == []:
        return False
    if raw is True:
        return True
    if not isinstance(raw, list) or not all(isinstance(h, str) and h.strip() for h in raw):
        raise CrewError(f"{source}: network is true, or a list of hosts such as [pypi.org, \"*.npmjs.org\"]")
    if backend.startswith("codex"):
        raise CrewError(f"{source}: Codex's sandbox can't keep to a list of hosts; give this bot network: true, or run "
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
    if spec.get("url"):
        transport = "sse" if spec.get("type") == "sse" else "http"
        return McpServer(url=str(spec["url"]), headers={k: str(v) for k, v in (spec.get("headers") or {}).items()},
                         transport=transport)
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
