"""What every backend takes and returns."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from crewbots.events import Event


class Access(StrEnum):
    """How much a run may touch. Each backend maps this onto its own permission or sandbox flags."""

    READ_ONLY = "read_only"  # read and search files, nothing else
    WORKSPACE_WRITE = "workspace_write"  # edit files and run commands in the working directory
    FULL = "full"  # no restrictions; only inside an isolated worktree or container


@dataclass(frozen=True)
class McpServer:
    """A stdio MCP server to start alongside the CLI. This is how crewbots gives bots its orchestration tools."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunSpec:
    prompt: str
    cwd: Path
    access: Access = Access.READ_ONLY
    model: str | None = None
    system_prompt: str | None = None  # appended to the CLI's own system prompt, never replacing it
    resume_session_id: str | None = None
    mcp_servers: dict[str, McpServer] = field(default_factory=dict)
    read_dirs: list[Path] = field(default_factory=list)  # outside cwd, readable even when read-only (e.g. skills)
    timeout_s: float | None = None
    extra_args: list[str] = field(default_factory=list)  # passed through to the CLI unchanged


class Backend(Protocol):
    name: str

    def run(self, spec: RunSpec) -> AsyncIterator[Event]:
        """Streams the run's events, ending with exactly one `Done`. Closing the iterator stops the CLI."""
        ...


def mcp_servers_json(servers: dict[str, McpServer]) -> dict[str, Any]:
    return {
        name: {"type": "stdio", "command": s.command, "args": s.args, "env": s.env}
        for name, s in servers.items()
    }
