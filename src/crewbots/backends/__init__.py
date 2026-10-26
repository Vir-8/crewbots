"""Coding CLIs that bots run on. `claude` is Claude Code through the Agent SDK and `codex` is Codex through its
app-server: both answer to crewbots' policy. `claude-cli` (plain `claude -p`) and `codex-exec` (one-shot `codex exec`)
are kept as fallbacks."""

from crewbots.backends.base import Access, Backend, McpServer, RunSpec
from crewbots.backends.claude_cli import ClaudeCLI
from crewbots.backends.claude_sdk import ClaudeAgent
from crewbots.backends.codex_cli import CodexCLI, CodexExec

BACKENDS: dict[str, type[ClaudeAgent] | type[ClaudeCLI] | type[CodexExec]] = {
    "claude": ClaudeAgent, "claude-cli": ClaudeCLI, "codex": CodexCLI, "codex-exec": CodexExec,
}

__all__ = ["BACKENDS", "Access", "Backend", "ClaudeAgent", "ClaudeCLI", "CodexCLI", "CodexExec", "McpServer", "RunSpec"]
