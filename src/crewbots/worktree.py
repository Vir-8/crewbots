"""One git worktree per task, so parallel bots never edit the same checkout.

Worktrees live under ~/.crewbots, not inside the repo, so they never show up as untracked files in it.
"""

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from crewbots import attribution, paths

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


def final_message(message: str) -> str:
    """The text to commit: `message` without AI attribution trailers (GitError if one would remain) and as Git's
    stripspace leaves it. Commit exactly this, and `verify_message` it afterwards."""
    try:
        return attribution.canonical(message)
    except attribution.AttributionError as e:
        raise GitError(str(e)) from None


NO_HOOKS = ("-c", "core.hooksPath=/dev/null")  # a hook could add a trailer after the message was checked


def verify_message(repo: Path, commit: str, message: str) -> None:
    """GitError unless `commit` has exactly the message that was checked and handed to Git."""
    try:
        attribution.verify_stored(repo, commit, message)
    except attribution.AttributionError as e:
        raise GitError(str(e)) from None


def commit(wt: Worktree, message: str) -> str | None:
    """Commits whatever is uncommitted. Returns the new commit, or None if there was nothing to commit."""
    message = final_message(message)
    _git(wt.path, "add", "--all")
    if not _git(wt.path, "status", "--porcelain"):
        return None
    _git(wt.path, *NO_HOOKS, "-c", "user.name=crewbots", "-c", "user.email=crewbots@localhost", "commit", "-q",
         "--no-verify", "--cleanup=whitespace", "-m", message)
    verify_message(wt.path, "HEAD", message)
    return _git(wt.path, "rev-parse", "HEAD")


def merge(wt: Worktree, message: str, *, tip: str | None = None, tested: tuple[str, str] | None = None) -> str:
    """Squashes the worktree's branch into whatever branch the main checkout has out, as one commit with `message`,
    and returns that commit (the current one, if the branch adds nothing). It refuses a checkout with uncommitted
    changes, and a conflict is reset, so it either merges cleanly or changes nothing. `tested` is the (branch, commit)
    CI ran against: if the checkout has since switched branch it
    refuses, and if that branch moved it raises Moved; either way nothing changes."""
    if _git(wt.repo, "status", "--porcelain", "--untracked-files=no"):
        raise GitError(f"{wt.repo} has uncommitted changes; commit or stash them, then merge again")
    try:
        into = _git(wt.repo, "symbolic-ref", "--short", "HEAD")
    except GitError:
        raise GitError(f"{wt.repo} is not on a branch (detached HEAD); check out the branch to merge into") from None
    if tested and into != tested[0]:  # never land on a branch nobody chose
        raise GitError(f"the checkout switched from {tested[0]} to {into} while CI ran, so nothing was merged; propose again")
    if tested and _git(wt.repo, "rev-parse", "--verify", f"refs/heads/{into}") != tested[1]:
        raise Moved(f"{into} moved while CI ran")
    message = final_message(message)  # the squashed commits' own messages are not carried over, only this one
    try:
        _git(wt.repo, "merge", "--squash", tip or wt.branch)
        committed = bool(subprocess.run([paths.git(), "diff", "--cached", "--quiet"], cwd=wt.repo).returncode)
        if committed:
            _git(wt.repo, *NO_HOOKS, "commit", "-q", "--no-verify", "--cleanup=whitespace", "-m", message)
    except GitError as e:
        subprocess.run([paths.git(), "reset", "--merge"], cwd=wt.repo, capture_output=True)
        raise GitError(f"merging {wt.branch} into {into} failed, and nothing was changed: {e}") from None
    if committed:
        verify_message(wt.repo, "HEAD", message)
    return _git(wt.repo, "rev-parse", "HEAD")


def branch_merged(repo: Path, branch_tip: str, merged_tip: str | None, merged_commit: str) -> None:
    """Raises GitError unless the branch holds nothing beyond what was merged. A squash commit does not have the
    branch in its history, so the recorded tip is what counts; records without one come from merge commits."""
    if merged_tip:
        _git(repo, "merge-base", "--is-ancestor", branch_tip, merged_tip)
    else:
        _git(repo, "merge-base", "--is-ancestor", branch_tip, merged_commit)


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


def copy_for_review(wt: Worktree, dest: Path) -> Path:
    """A throwaway checkout of the work as it stands, for a reviewer to run things in without touching it: detached at
    the task branch's tip, with the work's uncommitted changes and new files over it (ignored ones, such as
    dependencies, aren't copied). What the reviewer changes there never reaches the work."""
    tip = _git(wt.repo, "rev-parse", f"refs/heads/{wt.branch}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(wt.repo, "worktree", "add", "--detach", str(dest), tip)
    changed = _git(wt.path, "diff", "--name-only", "-z", "--no-renames", "HEAD", strip=False).split("\0")
    new = _git(wt.path, "ls-files", "-z", "--others", "--exclude-standard", strip=False).split("\0")
    for name in filter(None, [*changed, *new]):
        source, target = wt.path / name, dest / name
        if source.is_symlink() or source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
        else:  # deleted in the work
            target.unlink(missing_ok=True)
    return dest


def remove_copy(repo: Path, path: Path) -> None:
    """Remove a review's throwaway checkout, whatever the reviewer left in it."""
    if path.exists():
        _git(repo, "worktree", "remove", "--force", str(path))
    _git(repo, "worktree", "prune")


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


class Moved(GitError):
    """The branch CI tested moved, or the checkout switched branch, before the merge landed."""
