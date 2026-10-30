"""Keeping the user's own logins out of a writing bot's shell.

A writing bot's commands run as the user, so by default they'd use the user's GitHub login (`gh`, whose token is in
the keychain), git's credential helpers (Apple's git asks the keychain), and SSH keys. With the network on, that's
enough to push, open pull requests or comment as the user, which only the user does (team/rules.md). So every writing
bot's shell gets an environment without them: `gh` sees an empty config, git has no credential helper and an SSH
command that offers no key, and there's no SSH agent. The user's name and email stay, so commits are still theirs.

This takes away the ways a bot would sign in during ordinary work. On its own it isn't a wall: a command set on it
could still read a key file. So the files themselves are kept from every sandboxed bot (`private_paths`): the user's
logins and keys, and crewbots' own (the daemon's admin token, the MCP servers' settings with their keys, and the
crew's records). Claude and Codex keep their sandboxed commands out of them, and the policy keeps Claude's file tools
out. A bot with full access has no sandbox, so nothing here holds it back.
"""

import subprocess
from pathlib import Path

from crewbots import paths

# Under the home folder: logins, keys and the settings that carry them. A file that isn't there costs nothing.
LOGINS = (
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker/config.json", ".netrc", ".git-credentials", ".npmrc",
    ".pypirc", ".config/gh", ".config/hub", ".config/gcloud", ".cargo/credentials", ".cargo/credentials.toml",
    ".gem/credentials", ".terraform.d/credentials.tfrc.json", ".password-store", "Library/Keychains",
    ".codex/auth.json", ".codex/config.toml", ".claude/.credentials.json", ".claude.json",
    ".gemini",
    "Library/Application Support/Claude/claude_desktop_config.json",
)
TOKENS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS")
NO_KEY_SSH = "ssh -F /dev/null -o IdentitiesOnly=yes -o IdentityFile=/dev/null -o IdentityAgent=none -o BatchMode=yes"


def private_paths() -> tuple[Path, ...]:
    """What a sandboxed bot never reads, files and folders alike: crewbots' own keys and records, and the user's
    logins (LOGINS). The rest of the disk stays readable, the crew's team, skills and attachments included."""
    crew = paths.crew()
    return (
        crew / "daemon.json", crew / "mcp.json", *(crew / f"crewbots.db{end}" for end in ("", "-wal", "-shm")),
        paths.logs(), paths.root() / "browser", paths.root() / "antigravity", *(paths.home() / login for login in LOGINS),
    )


def without_credentials(folder: Path | None = None) -> dict[str, str]:
    """Environment variables for a writing bot's process. Empty values stand for unset, which every tool here treats
    the same. `folder` holds the empty logins: by default ~/.crewbots/no-credentials, outside anything a bot may
    write to."""
    folder = folder or paths.no_credentials()
    folder.mkdir(parents=True, exist_ok=True)
    gh = folder / "gh"
    gh.mkdir(exist_ok=True)
    gitconfig = folder / "gitconfig"
    lines = ["[user]"] + [f"\t{key} = {value}" for key in ("name", "email") if (value := _git_global(f"user.{key}"))]
    gitconfig.write_text("\n".join(lines) + "\n" if len(lines) > 1 else "")
    return {
        "GH_CONFIG_DIR": str(gh),
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        # Apple's git reads a credential helper from its own built-in config too; an empty helper clears the list.
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_SSH_COMMAND": NO_KEY_SSH,
        "GIT_TERMINAL_PROMPT": "0",
        "SSH_AUTH_SOCK": "",
        **{name: "" for name in TOKENS},
    }


def _git_global(key: str) -> str | None:
    try:
        done = subprocess.run([paths.git(), "config", "--global", "--get", key], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None
