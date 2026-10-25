"""One git worktree per task, so parallel bots never edit the same checkout.

Worktrees live under ~/.crewbots, not inside the repo, so they never show up as untracked files in it.
"""

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".crewbots" / "worktrees"
BRANCH_PREFIX = "crewbots/"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Worktree:
    repo: Path
    path: Path
    branch: str
    base: str  # the commit it started from, which its changes are measured against


def is_repo(path: Path) -> bool:
    result = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=path, capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() == "true"


def create(repo: Path, name: str, root: Path = DEFAULT_ROOT) -> Worktree:
    if not NAME.fullmatch(name):
        raise ValueError(f"worktree name {name!r} must be letters, digits, '.', '-' or '_'")
    top = Path(_git(repo, "rev-parse", "--show-toplevel"))
    base = _git(top, "rev-parse", "HEAD")
    repo_key = f"{top.name}-{hashlib.sha256(str(top).encode()).hexdigest()[:8]}"
    path = root / repo_key / name
    branch = BRANCH_PREFIX + name
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(top, "worktree", "add", "-b", branch, str(path), base)
    return Worktree(repo=top, path=path, branch=branch, base=base)


def changes(wt: Worktree) -> str:
    """Everything the bot changed since `base`, committed or not, new files included."""
    _git(wt.path, "add", "--all")
    return _git(wt.path, "diff", "--cached", wt.base, strip=False)


def commit(wt: Worktree, message: str) -> str | None:
    """Commits whatever is uncommitted. Returns the new commit, or None if there was nothing to commit."""
    _git(wt.path, "add", "--all")
    if not _git(wt.path, "status", "--porcelain"):
        return None
    _git(wt.path, "-c", "user.name=crewbots", "-c", "user.email=crewbots@localhost", "commit", "-q", "-m", message)
    return _git(wt.path, "rev-parse", "HEAD")


def remove(wt: Worktree, delete_branch: bool = False) -> None:
    _git(wt.repo, "worktree", "remove", "--force", str(wt.path))
    if delete_branch:
        _git(wt.repo, "branch", "-D", wt.branch)


def _git(cwd: Path, *args: str, strip: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip() if strip else result.stdout


class GitError(RuntimeError):
    pass
