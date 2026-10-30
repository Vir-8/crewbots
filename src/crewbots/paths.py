"""Where crewbots keeps things on this Mac, and where it reads other apps' settings.

Each is worked out when it's asked for, from the home folder at that moment, rather than when a module is imported.
So pointing HOME somewhere else (as the tests do) moves every one of them, and nothing can quietly bind the real
home folder at import.
"""

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path


def home() -> Path:
    return Path.home()


def root() -> Path:
    """crewbots' own folder: the crew, worktrees, logs and the rest."""
    return home() / ".crewbots"


def crew() -> Path:
    """The default crew, unless --home or CREWBOTS_HOME names another."""
    return Path(os.environ["CREWBOTS_HOME"]) if os.environ.get("CREWBOTS_HOME") else root() / "crew"


def worktrees() -> Path:
    return root() / "worktrees"


def review_worktrees() -> Path:
    """A pull request's head, checked out beside the user's own copy of its repository."""
    return worktrees() / "reviews"


def review_snapshots() -> Path:
    """Code review's own blobless clones and checkouts, and the changed files fetched from GitHub."""
    return root() / "reviews"


def bot_folders() -> Path:
    """Where each bot's own folder is, unless its role card names one: its chat works there, and what it makes for
    itself (drafts, notes, renders) lands there, where you can find it."""
    return home() / "Documents" / "Crewbots"


def logs() -> Path:
    return root() / "logs"


def temp() -> Path:
    """Sessions' private temp folders."""
    return root() / "tmp"


def caches() -> Path:
    """What writing bots' package managers download, kept between their tasks (backends/base.py)."""
    return root() / "caches"


def no_credentials() -> Path:
    """The empty logins a writing bot's shell gets (backends/credentials.py)."""
    return root() / "no-credentials"


def antigravity() -> Path:
    """The Antigravity runtime's own profile, with its sign-in, unless CREWBOTS_ANTIGRAVITY_HOME names another."""
    return Path(os.environ.get("CREWBOTS_ANTIGRAVITY_HOME") or root() / "antigravity")


def codex_servers() -> Path:
    """The last list of the user's Codex MCP servers that worked (backends/codex_isolation.py)."""
    return root() / "codex-mcp-servers.json"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or home() / ".codex")


def launch_agents() -> Path:
    return home() / "Library" / "LaunchAgents"


def global_skill_dirs() -> tuple[Path, ...]:
    """Skills installed for the user everywhere, which every bot can use too: Claude Code's (with the ones synced from
    claude.ai), the cross-agent folder, and Codex's."""
    return home() / ".claude" / "skills", home() / ".agents" / "skills", codex_home() / "skills"


def claude_code_config() -> Path:
    return home() / ".claude.json"


def claude_desktop_config() -> Path:
    return home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


def codex_config() -> Path:
    return codex_home() / "config.toml"


@functools.cache
def git() -> str:
    """The git to run. On a Mac, /usr/bin/git is a shim that looks the developer tools' git up again on every run,
    which takes longer than most runs themselves (18ms against 5ms), and crewbots runs git all the time: so the shim
    is asked once."""
    found = shutil.which("git") or "git"
    if sys.platform == "darwin" and found == "/usr/bin/git":
        try:
            real = subprocess.run(["/usr/bin/xcrun", "--find", "git"], capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            real = ""
        if real and os.access(real, os.X_OK):
            return real
    return found
