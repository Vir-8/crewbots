"""What every backend takes and returns."""

import tempfile
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from crewbots import paths
from crewbots.events import Event


class Access(StrEnum):
    """How much a run may touch. Each backend maps this onto its own permission or sandbox flags."""

    READ_ONLY = "read_only"  # read and search files, nothing else
    WORKSPACE_WRITE = "workspace_write"  # edit files and run commands in the working directory
    FULL = "full"  # no restrictions; only inside an isolated worktree or container


# Codex's error kinds (`codexErrorInfo`) for a turn that failed on the way to the model or back, after Codex's own
# retries: worth trying the turn again later. Its other kinds (context window, auth, bad request, policy, sandbox)
# would fail the same way again.
CODEX_TRANSIENT = {"serverOverloaded", "internalServerError", "flexUnavailable", "httpConnectionFailed",
                   "responseStreamConnectionFailed", "responseStreamDisconnected", "responseTooManyFailedAttempts"}
CODEX_LIMIT = {"usageLimitExceeded", "rateLimitExceeded"}


def claude_retry(status: int | None, error: str | None) -> str | None:
    """Whether a failed Claude turn is worth trying again, from the HTTP status of the API call that failed and the
    kind Claude Code gave the error. Claude Code has already retried the call itself by then."""
    if status == 429 or error == "rate_limit":
        return "limit"
    if (status or 0) >= 500 or error == "server_error":  # 529 is Anthropic's "overloaded"
        return "transient"
    return None


def codex_retry(info: Any) -> str | None:
    """The same for a failed Codex turn, from its `codexErrorInfo`: a kind's name, or an object keyed by it."""
    kind = next(iter(info), None) if isinstance(info, dict) else info
    return "limit" if kind in CODEX_LIMIT else "transient" if kind in CODEX_TRANSIENT else None


@dataclass(frozen=True)
class McpServer:
    """An MCP server a bot can use: a program the CLI starts (stdio), or one already running at a URL (http, or the
    older sse). The crew's own tools reach Codex this way too."""

    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    transport: str = "stdio"  # stdio, http or sse

    def config(self) -> dict[str, Any]:
        """The server in Claude's mcpServers shape, which the Agent SDK, `claude --mcp-config` and mcp.json all use."""
        if self.transport == "stdio":
            return {"type": "stdio", "command": self.command, "args": self.args, "env": self.env}
        return {"type": self.transport, "url": self.url, **({"headers": self.headers} if self.headers else {})}


@dataclass(frozen=True)
class RunSpec:
    prompt: str
    cwd: Path
    access: Access = Access.READ_ONLY
    model: str | None = None
    system_prompt: str | None = None  # appended to the CLI's own system prompt, never replacing it
    resume_session_id: str | None = None
    mcp_servers: dict[str, McpServer] = field(default_factory=dict)
    deny_read: list[Path] = field(default_factory=list)  # other bots' working-state files
    read_dirs: list[Path] = field(default_factory=list)  # outside cwd, readable even when read-only (e.g. skills)
    write_dirs: list[Path] = field(default_factory=list)  # outside cwd, where the user let it write, for this task
    crew: Any = None  # the crew tools as Python (crewbots.crew_tools.CrewTools), for backends that host them in-process
    timeout_s: float | None = None
    network: bool | tuple[str, ...] = False  # a writing bot's shell commands: no network, any host, or these hosts
    proactive: bool = False  # work the crew started on its own: nothing it does may reach outside crewbots
    effort: str | None = None  # reasoning effort (low ... max); None leaves it to the CLI's own default
    context_window: int | None = None  # tokens before the CLI compacts; None leaves it to the model's whole window
    extra_args: list[str] = field(default_factory=list)  # passed through to the CLI unchanged
    on_prompt_sent: Callable[[], None] | None = None  # called after the backend sends the prompt


class Session(Protocol):
    """A CLI kept running between turns, so a follow-up doesn't pay for start-up and keeps the CLI's state.

    One turn at a time. A turn that is abandoned part-way (its iterator closed) or that times out kills the process,
    since nothing would be left to read its output; the session is then dead, and the next turn opens a new one that
    resumes the conversation from its id.
    """

    session_id: str | None

    @property
    def alive(self) -> bool: ...

    def turn(self, prompt: str) -> AsyncIterator[Event]:
        """One turn's events, ending with exactly one `Done`."""
        ...

    async def close(self) -> None: ...


class Backend(Protocol):
    name: str

    def run(self, spec: RunSpec) -> AsyncIterator[Event]:
        """Streams the run's events, ending with exactly one `Done`. Closing the iterator stops the CLI."""
        ...

    async def open(self, spec: RunSpec) -> Session:
        """Starts a long-lived session for `spec` (its prompt is not used; each turn brings its own)."""
        ...


# Claude Code keeps an auto memory per working folder under ~/.claude/projects/, next to the user's own. A bot's notes
# are crewbots' (`remember`), so its sessions never read or write there.
CLAUDE_BOT_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

def session_temp_dir() -> Path:
    """A private temp folder for one session. With the shell confined to the task's folder, tools still need
    somewhere to write scratch files, and the machine's /tmp would be a way out of the folder."""
    paths.temp().mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="session-", dir=paths.temp()))


def package_caches() -> dict[str, str]:
    """Where a writing bot's package managers keep what they download, as environment variables: folders of crewbots'
    own that its sandbox may write in, since the user's (~/.cache/uv, ~/.npm) are outside it. The crew's bots share
    them, so a task in a new worktree installs from what an earlier one downloaded."""
    folders = {"UV_CACHE_DIR": paths.caches() / "uv", "npm_config_cache": paths.caches() / "npm"}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    return {name: str(folder) for name, folder in folders.items()}


def mcp_servers_json(servers: dict[str, McpServer]) -> dict[str, Any]:
    return {name: s.config() for name, s in servers.items()}
