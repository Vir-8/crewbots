"""crewbots' rules for what a bot may do, the same for every backend.

- A **read-only** bot reads. On Claude it gets no shell at all, since Claude has no OS sandbox. On Codex, whose
  read-only sandbox makes writes impossible, it may run commands, but not outward ones.
- A **writing** bot writes only inside its task's own folder (its worktree, when it has one).
- **No bot** pushes, merges, publishes or rewrites history: that's the user's (team/rules.md).
- A bot reaches only the MCP servers its role card grants, plus crewbots' own.
- Shell commands are confined by the OS (each CLI's sandbox) to the task's folder and a temp folder: a private one on
  Codex. Claude's sandbox also allows /tmp, which it needs for its own temp folder. On Codex every request to leave the
  sandbox is refused.
- The network is off for shell commands, unless the role card says `network: true` (any host) or lists the hosts.
  Claude's sandbox asks for each new host, and the list answers. Codex's sandbox only switches the network on or off,
  so a Codex bot takes `network: true` or nothing.

Claude asks through `can_use_tool` (`check_tool`); Codex asks through its approval requests (`check_command`,
`check_write`). Each refusal is kept, with its reason, so the backend can show it in the transcript.
"""

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from crewbots.backends.base import Access

READ_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# A guardrail, not a boundary: a determined command can always get around a pattern (containers are the real one).
OUTWARD = re.compile(
    r"\bgit\b(?:\s+-[Cc]\s+\S+|\s+--?[\w-]+(?:=\S+)?)*\s+(push|merge|rebase|reset\s+--hard|branch\s+-D|tag)\b"
    r"|\bgh\s+(pr\s+(create|merge)|release|repo\s+delete)\b"
    r"|\b(npm|pnpm|yarn|cargo|poetry|uv)\s+publish\b|\btwine\s+upload\b"
)
READ_ONLY = "this bot is read-only (its role card says access: read_only)"
USERS_TO_DO = "pushing, merging, publishing and rewriting history are the user's to do (team/rules.md)"
NO_NETWORK = "this bot has no network access; its role card can give it some with network: true, or a list of hosts"
STAY_IN_SANDBOX = "commands run inside this bot's sandbox, and stepping outside it isn't something a bot can grant itself"


class Policy:
    def __init__(self, access: Access, cwd: Path, granted_servers: set[str], network: bool | tuple[str, ...] = False):
        self.access = access
        self.cwd = Path(cwd).resolve()
        self.granted = granted_servers
        self.network = network
        self._refusals: list[str] = []

    # Claude: one decision per tool call.

    def check_tool(self, name: str, args: dict[str, Any]) -> str | None:
        """None to allow a Claude tool call, or why not."""
        if name.startswith("mcp__"):
            server = name.split("__")[1] if name.count("__") >= 2 else ""
            return None if server in self.granted else f"the MCP server {server!r} isn't granted by this bot's role card"
        if name in READ_TOOLS:
            return None
        if self.access is Access.READ_ONLY:
            return READ_ONLY
        if name in EDIT_TOOLS:
            return self.check_write(args.get("file_path") or args.get("notebook_path") or "")
        if name == "Bash":
            return self.check_command(str(args.get("command", "")), sandboxed=False)
        if name == "SandboxNetworkAccess":  # the sandbox asking for a host a command wants to reach
            return self.check_network(str(args.get("host", "")))
        return None

    # Codex, and the checks Claude's tool calls come down to.

    def check_command(self, command: str, sandboxed: bool) -> str | None:
        """None to allow a shell command, or why not. `sandboxed`: the OS keeps it from writing outside its folder."""
        if self.access is Access.READ_ONLY and not sandboxed:
            return READ_ONLY
        if OUTWARD.search(command):
            return USERS_TO_DO
        return None

    def check_network(self, host: str) -> str | None:
        if self.network is True or self.access is Access.FULL:
            return None
        if not self.network:
            return NO_NETWORK
        if any(fnmatch.fnmatch(host.lower(), pattern.lower()) for pattern in self.network):
            return None
        return f"{host} isn't one of the hosts this bot's role card allows ({', '.join(self.network)})"

    def check_write(self, path: str) -> str | None:
        if self.access is Access.READ_ONLY:
            return READ_ONLY
        if self.access is Access.FULL:
            return None
        if not path or not self._inside(path):
            return f"writes are limited to this task's own folder, {self.cwd}"
        return None

    # Keeping refusals, to show them.

    def refused(self, what: str, reason: str) -> str:
        self._refusals.append(f"Refused {what}: {reason}")
        return reason

    def take_refusals(self) -> list[str]:
        out, self._refusals = self._refusals, []
        return out

    def _inside(self, path: str) -> bool:
        target = Path(path) if os.path.isabs(path) else self.cwd / path
        try:
            return target.resolve().is_relative_to(self.cwd)
        except OSError:
            return False
