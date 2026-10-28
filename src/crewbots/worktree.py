"""One git worktree per task, so parallel bots never edit the same checkout.

Worktrees live under ~/.crewbots, not inside the repo, so they never show up as untracked files in it.
"""

import hashlib
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from crewbots import paths

BRANCH_PREFIX = "crewbots/"
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Worktree:
    repo: Path
    path: Path
    branch: str
    base: str  # the commit it started from, which its changes are measured against


def is_repo(path: Path) -> bool:
    result = subprocess.run([paths.git(), "rev-parse", "--is-inside-work-tree"], cwd=path, capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() == "true"


def create(repo: Path, name: str, root: Path | None = None) -> Worktree:
    if not NAME.fullmatch(name):
        raise ValueError(f"worktree name {name!r} must be letters, digits, '.', '-' or '_'")
    top = Path(_git(repo, "rev-parse", "--show-toplevel"))
    base = _git(top, "rev-parse", "HEAD")
    repo_key = f"{top.name}-{hashlib.sha256(str(top).encode()).hexdigest()[:8]}"
    path = (root or paths.worktrees()) / repo_key / name
    branch = BRANCH_PREFIX + name
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(top, "worktree", "add", "-b", branch, str(path), base)
    return Worktree(repo=top, path=path, branch=branch, base=base)


def restore(wt: Worktree, *, tip: str | None = None) -> None:
    """Reopen saved work when a task is resumed."""
    if tip:
        exists = subprocess.run([paths.git(), "show-ref", "--verify", "--quiet", f"refs/heads/{wt.branch}"],
                                cwd=wt.repo, capture_output=True)
        if exists.returncode == 1:
            _git(wt.repo, "worktree", "add", "-b", wt.branch, str(wt.path), tip)
            return
        if exists.returncode != 0:
            raise GitError(f"could not check task branch {wt.branch}: {exists.stderr.decode(errors='replace').strip()}")
        _git(wt.repo, "merge-base", "--is-ancestor", tip, wt.branch)
    _git(wt.repo, "worktree", "add", str(wt.path), wt.branch)


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


def merge(wt: Worktree, message: str, *, tip: str | None = None) -> str:
    """Merges the worktree's branch into whatever branch the main checkout has out, with a merge commit, and returns
    that commit. It refuses a checkout with uncommitted changes, and a conflict is aborted, so it either merges cleanly
    or changes nothing."""
    if _git(wt.repo, "status", "--porcelain", "--untracked-files=no"):
        raise GitError(f"{wt.repo} has uncommitted changes; commit or stash them, then merge again")
    try:
        into = _git(wt.repo, "symbolic-ref", "--short", "HEAD")
    except GitError:
        raise GitError(f"{wt.repo} is not on a branch (detached HEAD); check out the branch to merge into") from None
    try:
        _git(wt.repo, "merge", "--no-ff", "--no-edit", "-m", message, tip or wt.branch)
    except GitError as e:
        subprocess.run([paths.git(), "merge", "--abort"], cwd=wt.repo, capture_output=True)
        raise GitError(f"merging {wt.branch} into {into} failed, and nothing was changed: {e}") from None
    return _git(wt.repo, "rev-parse", "HEAD")


def git_read(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """A git call that leaves the repository as it was: no optional locks, so `status` doesn't refresh the index."""
    return _git(cwd, *args, env={"GIT_OPTIONAL_LOCKS": "0", **(env or {})})


def already_in(repo: Path, target: str, tip: str, base: str) -> bool:
    """Whether `tip` is already part of `target`: an ancestor of it, or a merge that would change nothing. Writes
    nothing to the repository: merge-tree's objects go to a temporary directory that is removed afterwards."""
    try:
        git_read(repo, "merge-base", "--is-ancestor", tip, target)
        return True
    except GitError as e:
        if "exit code 1" not in str(e):
            raise
    with tempfile.TemporaryDirectory(prefix="crewbots-merge-tree-") as scratch:
        objects = git_read(repo, "rev-parse", "--path-format=absolute", "--git-path", "objects")
        try:
            merged = git_read(repo, "merge-tree", "--write-tree", "--merge-base", base, target, tip,
                              env={"GIT_OBJECT_DIRECTORY": scratch, "GIT_ALTERNATE_OBJECT_DIRECTORIES": objects})
        except GitError:  # conflicts, or a merge base that isn't there: not all of it is in
            return False
        return merged.split("\n", 1)[0].strip() == git_read(repo, "rev-parse", f"{target}^{{tree}}")


def remove(wt: Worktree, delete_branch: bool = False, *, branch_tip: str | None = None) -> bool:
    """Remove a checkout; later branch/prune failures are warnings. Return whether the branch was deleted."""
    if delete_branch and branch_tip is None:
        branch_tip = _git(wt.repo, "rev-parse", f"refs/heads/{wt.branch}")
    _git(wt.repo, "worktree", "remove", str(wt.path))
    branch_deleted = False
    if delete_branch:
        try:
            _git(wt.repo, "update-ref", "-d", f"refs/heads/{wt.branch}", branch_tip)
            branch_deleted = True
        except (GitError, OSError):
            logging.getLogger(__name__).exception("Could not delete branch %s after worktree removal", wt.branch)
    try:
        _git(wt.repo, "worktree", "prune")
    except (GitError, OSError):
        logging.getLogger(__name__).exception("Could not prune worktrees after removing %s", wt.path)
    return branch_deleted


def _git(cwd: Path, *args: str, strip: bool = True, env: dict[str, str] | None = None) -> str:
    result = subprocess.run([paths.git(), *args], cwd=cwd, capture_output=True, text=True,
                            env=None if env is None else {**os.environ, **env})
    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        status = (f"terminated by signal {-result.returncode}" if result.returncode < 0
                  else f"exit code {result.returncode}")
        raise GitError(f"git {' '.join(args)} failed ({status})" + (f": {detail}" if detail else ""))
    return result.stdout.strip() if strip else result.stdout


class GitError(RuntimeError):
    pass
