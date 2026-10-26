"""Coding CLIs that bots run on. `claude` is Claude Code through the Agent SDK; `claude-cli` is the same CLI driven
as `claude -p`, kept as a fallback."""

from crewbots.backends.base import Access, Backend, McpServer, RunSpec
from crewbots.backends.claude_cli import ClaudeCLI
from crewbots.backends.claude_sdk import ClaudeAgent
from crewbots.backends.codex_cli import CodexCLI

BACKENDS: dict[str, type[ClaudeAgent] | type[ClaudeCLI] | type[CodexCLI]] = {
    "claude": ClaudeAgent, "claude-cli": ClaudeCLI, "codex": CodexCLI,
}

__all__ = ["BACKENDS", "Access", "Backend", "ClaudeAgent", "ClaudeCLI", "CodexCLI", "McpServer", "RunSpec"]
