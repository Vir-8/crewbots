"""Coding CLIs that bots run on."""

from crewbots.backends.base import Access, Backend, McpServer, RunSpec
from crewbots.backends.claude_cli import ClaudeCLI
from crewbots.backends.codex_cli import CodexCLI

BACKENDS: dict[str, type[ClaudeCLI] | type[CodexCLI]] = {"claude": ClaudeCLI, "codex": CodexCLI}

__all__ = ["BACKENDS", "Access", "Backend", "ClaudeCLI", "CodexCLI", "McpServer", "RunSpec"]
