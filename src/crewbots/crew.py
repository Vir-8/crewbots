"""A crew is a folder: one role card per bot in `bots/`, the shared brain every bot reads in `team/`, and the skills
bots can use in `skills/` (Agent Skills: a folder per skill with a SKILL.md, the format pstack and others ship in).

A role card is Markdown with YAML frontmatter. The frontmatter is what the harness enforces (backend, access, MCP
servers); the body is the card itself (mission, owns, does not own, done means), given to the bot as instructions.
"""

import shutil
from dataclasses import dataclass, field, replace
from datetime import timedelta
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from crewbots.backends import BACKENDS, Access, McpServer
from crewbots.memory import MEMORY_GUIDE, Memory

DEFAULT_HOME = Path.home() / ".crewbots" / "crew"
CARD_FIELDS = {
    "name", "title", "description", "backend", "model", "access", "workspace", "worktree", "timeout", "mcp",
    "can_delegate", "skills", "budget", "network",
}
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
    mcp: dict[str, McpServer] = field(default_factory=dict)
    can_delegate: bool = False  # only coordinators assign work; the harness refuses delegation from anyone else
    skills: tuple[str, ...] = ()  # names from the crew's skills/ folder, or ("all",)
    budget: Budget | None = None
    network: bool | tuple[str, ...] = False  # for its shell commands: none, any host, or these hosts
    path: Path | None = None  # its role card


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path  # the SKILL.md; bots read it when a task matches


@dataclass(frozen=True)
class Crew:
    home: Path
    bots: dict[str, Bot]
    team_files: dict[str, str]  # file name → contents, from team/
    skills: dict[str, Skill] = field(default_factory=dict)

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    def skills_for(self, bot: Bot) -> list[Skill]:
        if "all" in bot.skills:
            return list(self.skills.values())
        return [self.skills[name] for name in bot.skills]

    @property
    def memory(self) -> Memory:
        return Memory(self.home)

    @property
    def routines_dir(self) -> Path:
        return self.home / "routines"

    def system_prompt(self, bot: Bot, delegated_by: str | None = None, reviewing: str | None = None,
                      briefing: str | None = None, routine: str | None = None, setting: str | None = None) -> str:
        roster = "\n".join(
            f"- {b.name} ({b.title}){' ← you' if b.name == bot.name else ''}: {b.description}" for b in self.bots.values()
        )
        parts = [
            f"You are {bot.name}, the {bot.title} of a crew of AI agents run by crewbots.",
            setting if setting else
            f"This task was delegated to you by {delegated_by}; your handoff goes back to them."
            if delegated_by else
            f"The crew's routine {routine!r} started this task, not a message from the user. Your final message is what "
            "the user reads, when they next look; keep it short, and lead with anything that needs them."
            if routine else "This task comes from the user; your final message is what they read.",
            f"# Your role card\n\n{bot.instructions.strip()}",
            f"# The crew\n\n{roster}",
            CREW_TOOLS + ("\n\n" + DELEGATION_TOOLS if bot.can_delegate else ""),
        ]
        if reviewing:
            parts.insert(2, f"# This review\n\n{reviewing}")
        if briefing:
            parts.insert(2, "# Where things stand\n\nThis task's conversation grew long, so it continues in a fresh session. "
                         f"This is the briefing you wrote for it:\n\n{briefing}")
        notes = self.memory.prompt(bot.name)
        parts.append(MEMORY_GUIDE + (f"\n\n{notes}" if notes else "\n\nNo notes yet."))
        if skills := self.skills_for(bot):
            listing = "\n".join(f"- {s.name}: {s.description} ({s.path})" for s in skills)
            parts.append(
                "# Skills\n\nSkills are tested procedures. When a task matches one, read its SKILL.md first and follow "
                f"it. Where a skill says to spawn subagents, you may use your own; across bots, delegate.\n\n{listing}"
            )
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
        bot = replace(parse_card(path.read_text(), default_name=path.stem, source=path), path=path)
        if bot.name in bots:
            raise CrewError(f"{path}: a bot named {bot.name!r} already exists")
        bots[bot.name] = bot
    if not bots:
        raise CrewError(f"no role cards in {bots_dir}")
    team_dir = home / "team"
    team_files = {p.name: p.read_text() for p in sorted(team_dir.glob("*.md"))} if team_dir.is_dir() else {}
    skills = load_skills(home / "skills")
    for bot in bots.values():
        if missing := [s for s in bot.skills if s != "all" and s not in skills]:
            raise CrewError(f"{bot.name}: no skills named {missing} in {home / 'skills'}")
    return Crew(home=home, bots=bots, team_files=team_files, skills=skills)


def stamp(home: Path) -> tuple[tuple[str, int], ...]:
    """What the crew is made of on disk, as names and modification times: when it changes, the crew is read again."""
    home = home.expanduser()
    paths = [*home.glob("bots/*.md"), *home.glob("team/*.md"), *home.glob("skills/**/SKILL.md")]
    found = []
    for p in sorted(paths):
        try:
            found.append((str(p.relative_to(home)), p.stat().st_mtime_ns))
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
        mcp=_mcp(meta.get("mcp") or {}, source),
        can_delegate=bool(meta.get("can_delegate", False)),
        skills=_skill_names(meta.get("skills"), source),
        budget=_budget(meta.get("budget"), source),
        network=_network(meta.get("network", False), access, backend, source),
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


def _network(raw: Any, access: Access, backend: str, source: Path | str) -> bool | tuple[str, ...]:
    if raw is False or raw is None or raw == []:
        return False
    if access is Access.READ_ONLY:
        raise CrewError(f"{source}: network is for a writing bot's shell commands; this bot is read-only")
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
    if raw == "all":
        return ("all",)
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise CrewError(f"{source}: skills must be a list of skill names, or all")
    return tuple(raw)


def _mcp(raw: dict[str, Any], source: Path | str) -> dict[str, McpServer]:
    """The same shape as Claude's mcpServers config, so entries can be copied from it."""
    servers = {}
    for name, spec in raw.items():
        if name == "crewbots":
            raise CrewError(f"{source}: the mcp server name crewbots is reserved for the crew's own tools")
        if not isinstance(spec, dict) or "command" not in spec:
            raise CrewError(f"{source}: mcp server {name!r} needs a command")
        servers[name] = McpServer(
            command=spec["command"], args=[str(a) for a in spec.get("args", [])],
            env={k: str(v) for k, v in (spec.get("env") or {}).items()},
        )
    return servers
