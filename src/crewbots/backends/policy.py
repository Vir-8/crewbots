"""crewbots' rules for what a bot may do, the same for every backend.

- A **read-only** bot reads. On Claude it gets no shell at all, since Claude has no OS sandbox. On Codex, whose
  read-only sandbox makes writes impossible, it may run commands, but not outward ones.
- A **writing** bot writes only inside its task's own folder (its worktree, when it has one).
- **No bot** pushes, merges, publishes or rewrites history: that's the user's (team/rules.md).
- A bot reaches only the MCP servers its role card grants, plus crewbots' own.
- Shell commands are confined by the OS (each CLI's sandbox) to the task's folder and a temp folder: a private one on
  Codex. Claude's sandbox also allows /tmp, which it needs for its own temp folder. On Codex every request to leave the
  sandbox is refused.
- **No bot** short of full access reads what's private (backends/credentials.py): the user's logins and keys, and
  crewbots' own, its admin token above all. Claude's file tools are refused them here and by Claude Code's own rules,
  and both CLIs' sandboxes keep commands out of them.
- Work a bot starts on its own (a check-in, a thread from one) reaches nothing outside crewbots: it gets none of the
  bot's MCP servers, only crewbots' own (runner/core.py).
- The network is off for shell commands, unless the role card says `network: true` (any host) or lists the hosts.
  Claude's sandbox asks for each new host, and the list answers. Codex's sandbox only switches the network on or off,
  so a Codex bot takes `network: true` or nothing.
- What a bot asks the user for (`ask_permission`) and gets is its task's, for the rest of it: writing in a folder
  outside its own (`write_dirs`; a read-only bot then writes there and nowhere else), and the network. Never the
  private paths, and never what's the user's to do.

Claude asks through `can_use_tool` (`check_tool`); Codex asks through its approval requests (`check_command`,
`check_write`). Each refusal is kept, with its reason, so the backend can show it in the transcript.
"""

import fnmatch
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from crewbots.backends.base import Access
from crewbots.backends.credentials import private_paths

READ_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite"}
# Claude Code runs these itself, outside the shell's sandbox, so they follow the network setting on their own.
WEB_TOOLS = {"WebFetch", "WebSearch"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
LOOKUP_TOOLS = {"ToolSearch"}  # finds an MCP tool's definition; using the tool is checked on its own
# A guardrail, not a boundary: a determined command can always get around a pattern (containers are the real one).
OUTWARD = re.compile(
    r"\bgit\b(?:\s+-[Cc]\s+\S+|\s+--?[\w-]+(?:=\S+)?)*\s+(push|merge|rebase|reset\s+--hard|branch\s+-D|tag)\b"
    r"|\bgh\s+(pr\s+(create|merge)|release|repo\s+delete)\b"
    r"|\b(npm|pnpm|yarn|cargo|poetry|uv)\s+publish\b|\btwine\s+upload\b"
)
ASK = "ask the user with ask_permission"
READ_ONLY = f"this bot is read-only (its role card says access: read_only); to write somewhere, {ASK}"
USERS_TO_DO = "pushing, merging, publishing and rewriting history are the user's to do (team/rules.md)"
NO_NETWORK = f"this bot has no network access; if this task needs it, {ASK}"
SEARCH_NEEDS_ANY_HOST = "a web search can lead anywhere, and this bot's network is limited to some hosts"
PRIVATE = "that's private: the user's logins and keys, or crewbots' own; no bot reads it"
STAY_IN_SANDBOX = f"commands run inside this bot's sandbox, which a bot can't widen itself; if this task needs more, {ASK}"


class Policy:
    def __init__(self, access: Access, cwd: Path, granted_servers: set[str], network: bool | tuple[str, ...] = False,
                 write_dirs: tuple[Path, ...] | list[Path] = (), deny_read: tuple[Path, ...] | list[Path] = ()):
        self.access = access
        self.cwd = Path(cwd).resolve()
        self.granted = granted_servers
        self.network = network
        self.write_dirs = tuple(Path(d).resolve() for d in write_dirs)
        self.private = tuple(p.resolve() for p in (*private_paths(), *deny_read))
        self.deny_read = tuple(p.resolve() for p in deny_read)
        self._refusals: list[str] = []

    # Claude: one decision per tool call.

    def check_tool(self, name: str, args: dict[str, Any]) -> str | None:
        """None to allow a Claude tool call, or why not."""
        if name.startswith("mcp__"):
            server = name.split("__")[1] if name.count("__") >= 2 else ""
            if server not in self.granted:
                return f"the MCP server {server!r} isn't granted by this bot's role card"
            return None
        if name in READ_TOOLS:
            return self.check_read(args)
        if name in LOOKUP_TOOLS:
            return None
        if name in WEB_TOOLS:
            return self.check_web(name, args)
        if name in EDIT_TOOLS:
            return self.check_write(args.get("file_path") or args.get("notebook_path") or "")
        if self.access is Access.READ_ONLY:
            return READ_ONLY
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

    def check_web(self, name: str, args: dict[str, Any]) -> str | None:
        """A web search or page fetch: any host with network on, the listed hosts' pages with a list, nothing off."""
        if self.network is True or self.access is Access.FULL:
            return None
        if not self.network:
            return NO_NETWORK
        if name == "WebFetch":
            return self.check_network(urlparse(str(args.get("url", ""))).hostname or "")
        return SEARCH_NEEDS_ANY_HOST

    def check_read(self, args: dict[str, Any]) -> str | None:
        """A file tool's read: anywhere but the private paths. A search names the folder it starts from, or a pattern
        whose folder part is where it looks."""
        pattern = os.path.expanduser(str(args.get("pattern", "")))
        where = pattern.split("*")[0] if "*" in pattern else ""
        for target in (args.get("file_path"), args.get("notebook_path"), args.get("path"), where):
            if target and self._private(str(target)):
                return PRIVATE
        if self.deny_read and ("pattern" in args or "path" in args):
            base = Path(os.path.expanduser(str(args.get("path") or where or self.cwd)))
            base = (base if base.is_absolute() else self.cwd / base).resolve()
            if any(path.is_relative_to(base) for path in self.deny_read):
                return PRIVATE
        return None

    def check_write(self, path: str) -> str | None:
        """A write: in the task's own folder, or in a folder the user allowed it (a read-only bot only there)."""
        if path and any(self._inside(path, private) for private in self.deny_read):
            return PRIVATE
        if self.access is Access.FULL:
            return None
        if path and self._private(path):
            return PRIVATE
        if path and any(self._inside(path, folder) for folder in self.write_dirs):
            return None
        if self.access is Access.READ_ONLY:
            return READ_ONLY
        if not path or not self._inside(path, self.cwd):
            return f"writes are limited to this task's own folder, {self.cwd}; to write elsewhere, {ASK}"
        return None

    # Keeping refusals, to show them.

    def refused(self, what: str, reason: str) -> str:
        self._refusals.append(f"Refused {what}: {reason}")
        return reason

    def take_refusals(self) -> list[str]:
        out, self._refusals = self._refusals, []
        return out

    def _private(self, path: str) -> bool:
        target = Path(os.path.expanduser(path))
        target = target if target.is_absolute() else self.cwd / target
        try:
            target = target.resolve()
        except OSError:
            return True
        paths = self.deny_read if self.access is Access.FULL else self.private
        return any(target.is_relative_to(p) for p in paths)

    def _inside(self, path: str, folder: Path) -> bool:
        target = Path(path) if os.path.isabs(path) else self.cwd / path
        try:
            return target.resolve().is_relative_to(folder)
        except OSError:
            return False
