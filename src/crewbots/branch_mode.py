"""Git operations for a task that holds a project's own checkout.

These helpers do not acquire or release the project hold; the runner must do that
before calling them and persist the returned metadata before starting a turn.
"""

import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from crewbots import paths, worktree


@dataclass(frozen=True)
class BranchCheckout:
    repo: Path
    branch: str
    original_branch: str
    base: str


def prepare(repo: Path, task_id: str) -> BranchCheckout:
    """Inspect a clean checkout without changing it."""
    if not worktree.NAME.fullmatch(task_id):
        raise ValueError(f"invalid task id {task_id!r}")
    requested = repo.resolve()
    repo = Path(worktree._git(repo, "rev-parse", "--show-toplevel")).resolve()
    if requested != repo:
        raise worktree.GitError(f"{requested} is not the repository root {repo}; branch mode cannot use it")
    if worktree._git(repo, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none",
                     "--", ".", ":(exclude)Temp/UnityLockfile"):
        raise worktree.GitError(f"{repo} has uncommitted or untracked changes")
    original = worktree._git(repo, "symbolic-ref", "--short", "HEAD")
    base = worktree._git(repo, "rev-parse", "HEAD")
    branch = worktree.BRANCH_PREFIX + task_id
    return BranchCheckout(repo, branch, original, base)


def start(repo: Path, task_id: str) -> BranchCheckout:
    checkout = prepare(repo, task_id)
    worktree._git(checkout.repo, "switch", "-c", checkout.branch)
    return checkout


def on_branch(checkout: BranchCheckout) -> bool:
    try:
        if worktree._git(checkout.repo, "symbolic-ref", "--short", "HEAD") != checkout.branch:
            return False
        worktree._git(checkout.repo, "rev-parse", "--verify", f"refs/heads/{checkout.branch}")
        return True
    except worktree.GitError:
        return False


def turn_head(checkout: BranchCheckout) -> str:
    if not on_branch(checkout):
        raise worktree.GitError("HEAD is not on the task branch")
    return worktree._git(checkout.repo, "rev-parse", "HEAD")


def turn_safe(checkout: BranchCheckout, start: str) -> bool:
    if not on_branch(checkout):
        return False
    try:
        worktree._git(checkout.repo, "merge-base", "--is-ancestor", start, "HEAD")
        return True
    except worktree.GitError:
        return False


def is_ancestor(repo: Path, commit: str, tip: str) -> bool:
    result = subprocess.run([paths.git(), "merge-base", "--is-ancestor", commit, tip], cwd=repo, capture_output=True)
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise worktree.GitError(f"could not check merge ancestry: {result.stderr.decode(errors='replace').strip()}")


def switch_head(repo: Path, old: str, new: str) -> None:
    """Move HEAD only if it still names the expected branch."""
    source, target = f"refs/heads/{old}", f"refs/heads/{new}"
    command = f"start\noption no-deref\nsymref-update HEAD {target} ref {source}\nprepare\ncommit\n"
    result = subprocess.run([paths.git(), "update-ref", "--stdin"], cwd=repo, input=command,
                            capture_output=True, text=True)
    if not result.returncode:
        return
    if "unknown command" not in result.stderr:
        raise worktree.GitError(f"HEAD switch failed: {result.stderr.strip()}")
    # Git without symref-update: index.lock blocks a concurrent git switch; recheck before writing HEAD.
    if worktree._git(repo, "symbolic-ref", "--short", "HEAD") != old:
        raise worktree.GitError("HEAD changed during merge; leaving it alone")
    worktree._git(repo, "symbolic-ref", "HEAD", target)


def index_lock_path(repo: Path) -> tuple[Path, Path]:
    index = Path(worktree._git(repo, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = repo / index
    return index, Path(str(index) + ".lock")


LOCKFILE = "Temp/UnityLockfile"
LOCK_PREFIX = "crewbots-lock "
LOCK_LEFT = "index.lock left in place; it may be stale; crewbots did not create it"
LOCK_STUCK = "index.lock was created by crewbots but could not be removed"
LOCK_TEMP = "crewbots-lock-tmp-"
INDEX_TEMP = "crewbots-index-new-"


def new_nonce() -> str:
    return f"{time.time_ns()}:{secrets.token_hex(16)}"


def temp_prefix(kind: str, nonce: str) -> str:
    """Temporary file names carry the nonce (without characters some filesystems refuse), so a sweep finds only ours."""
    return f"{kind}{nonce.replace(':', '-')}-"


def take_lock(lock: Path, nonce: str) -> None:
    """Create index.lock already holding our prefix and nonce: link a finished file, so it is ours from its first instant."""
    fd, temp = tempfile.mkstemp(dir=lock.parent, prefix=temp_prefix(LOCK_TEMP, nonce))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(f"{LOCK_PREFIX}{nonce}\n")
        os.link(temp, lock)  # fails if index.lock exists
    finally:
        try:
            Path(temp).unlink(missing_ok=True)
        except OSError as e:  # the lock, if linked, is ours regardless; the sweep removes the leftover
            logging.getLogger(__name__).warning("Could not remove temporary lock file %s: %s", temp, e)


def lock_is_ours(lock: Path, nonce: str | None) -> bool:
    """Whether index.lock exists and holds our prefix and the recorded nonce (any nonce if none was recorded)."""
    try:
        with open(lock, "rb") as f:
            text = f.read(512).decode("utf-8", "replace")
    except OSError:
        return False
    return text.startswith(LOCK_PREFIX) and (not nonce or text == f"{LOCK_PREFIX}{nonce}\n")


def clear_interrupted_lock(lock: Path, nonce: str | None) -> bool:
    """Remove index.lock only if it is ours. True when it is gone; a foreign lock, or ours that will not go, is left."""
    if not lock.exists() and not lock.is_symlink():
        return True
    if not lock_is_ours(lock, nonce):
        logging.getLogger(__name__).warning("%s: %s", LOCK_LEFT, lock)
        return False
    try:
        lock.unlink(missing_ok=True)
    except OSError as e:
        logging.getLogger(__name__).warning("%s: %s: %s", LOCK_STUCK, lock, e)
        return False
    return True


def lock_problem(lock: Path, nonce: str | None) -> str:
    return LOCK_STUCK if lock_is_ours(lock, nonce) else LOCK_LEFT


def sweep_temp_files(repo: Path, nonce: str | None) -> None:
    """Remove the temporary files of the merge with this nonce. Without a recorded nonce, remove nothing and say what is there."""
    _, lock = index_lock_path(repo)
    log = logging.getLogger(__name__)
    if not nonce:
        found = [f.name for kind in (LOCK_TEMP, INDEX_TEMP) for f in lock.parent.glob(kind + "*")]
        if found:
            log.warning("Temporary crewbots files left in %s (no nonce recorded, so not removed): %s", lock.parent, found)
        return
    for kind in (LOCK_TEMP, INDEX_TEMP):
        for leftover in lock.parent.glob(temp_prefix(kind, nonce) + "*"):
            try:
                leftover.unlink(missing_ok=True)
            except OSError as e:
                log.warning("Could not remove %s: %s", leftover, e)


def seed_index(repo: Path, target: Path) -> bool:
    """Copy the real index to target so git keeps its stat cache and rehashes only changed files.

    The caller resets the copy's entries to HEAD (`read-tree -m HEAD`), which keeps stat data only where content matches.

    False (nothing copied) when the index is split or sparse, or any entry is marked skip-worktree or
    assume-unchanged, since `add --all` would then leave those entries as they are instead of rereading the files.
    """
    try:
        index, _ = index_lock_path(repo)
        common = Path(worktree._git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        if any(list(d.glob("sharedindex.*")) for d in (index.parent, common)):
            return False
        if _config_on(repo):
            return False
        flags = worktree._git(repo, "ls-files", "-v", "-z", strip=False).split("\0")
        if any(f and (f[0].islower() or f[0] == "S") for f in flags):
            return False
        shutil.copy2(index, target)  # keeps the mtime git compares entries against to spot racily clean files
        return True
    except (OSError, worktree.GitError):
        target.unlink(missing_ok=True)
        return False


def _config_on(repo: Path) -> bool:
    """Whether a setting that changes what the index holds is on. A failed query counts as on; exit 1 only means unset."""
    for key in ("core.splitIndex", "core.sparseCheckout", "index.sparse"):
        result = subprocess.run([paths.git(), "config", "--type=bool", "--get", key], cwd=repo, capture_output=True, text=True)
        if result.returncode not in (0, 1):
            raise worktree.GitError(f"git config {key} failed: {result.stderr.strip()}")
        if result.stdout.strip() == "true":
            return True
    return False


@contextmanager
def snapshot(checkout: BranchCheckout):
    """Build a temporary index containing the current files without touching the real index."""
    fd, name = tempfile.mkstemp(prefix="crewbots-index-")
    os.close(fd)
    os.unlink(name)
    env = dict(os.environ, GIT_INDEX_FILE=name)

    def git(*args: str, input: str | None = None) -> str:
        result = subprocess.run([paths.git(), *args], cwd=checkout.repo, env=env, input=input,
                                capture_output=True, text=True)
        if result.returncode:
            raise worktree.GitError(result.stderr.strip() or result.stdout.strip() or "git failed")
        return result.stdout

    def build(seeded: bool) -> None:
        Path(name).unlink(missing_ok=True)
        if seeded and seed_index(checkout.repo, Path(name)):
            git("read-tree", "-m", "HEAD")  # the entries are HEAD's, as without seeding; only the stat data is kept
        else:
            Path(name).unlink(missing_ok=True)
            git("read-tree", "HEAD")
        git("add", "--all")  # not an exclude pathspec: git add refuses one when an ignored Temp/ exists
        git("reset", "-q", "HEAD", "--", LOCKFILE)  # back to HEAD's state: absent, or HEAD's own entry

    try:
        try:
            build(True)
        except worktree.GitError:
            build(False)
        yield git, Path(name)
    finally:
        Path(name).unlink(missing_ok=True)


def changed_files(checkout: BranchCheckout) -> list[str]:
    if not on_branch(checkout):
        raise worktree.GitError("HEAD is not on the task branch")
    with snapshot(checkout) as (git, _):
        return [x for x in git("diff", "--cached", "--no-renames", "--name-only", "-z", checkout.base).split("\0") if x]


def leftover_files(repo: Path) -> list[str]:
    """Files still uncommitted after the merge snapshot, including staged and untracked files."""
    pathspec = ("--", ".", ":(exclude)Temp/UnityLockfile")
    commands = (("diff", "--no-ext-diff", "--no-renames", "--ignore-submodules=none", "--name-only", "-z"),
                ("diff", "--cached", "--no-ext-diff", "--no-renames", "--ignore-submodules=none", "--name-only", "-z"),
                ("ls-files", "--others", "--exclude-standard", "-z"))
    files = {name for args in commands for name in worktree._git(repo, *args, *pathspec, strip=False).split("\0") if name}
    return sorted(files)


def merge(checkout: BranchCheckout, message: str, *, expected_files: list[str] | None = None,
          before_lock=None, before_refs=None) -> tuple[str, str]:
    """Snapshot the task and fast-forward the original branch to one squashed commit."""
    if not on_branch(checkout):
        raise worktree.GitError("HEAD is not on the task branch")
    ref = f"refs/heads/{checkout.original_branch}"
    if worktree._git(checkout.repo, "rev-parse", "--verify", ref) != checkout.base:
        raise worktree.GitError(f"{checkout.original_branch} moved since the task started; merge stopped")
    with snapshot(checkout) as (git, index):
        files = [x for x in git("diff", "--cached", "--no-renames", "--name-only", "-z", checkout.base).split("\0") if x]
        if expected_files is not None and files != expected_files:
            raise worktree.GitError("Changed files have changed since the proposal; propose the merge again")
        tree = git("write-tree").strip()
        head = git("rev-parse", "HEAD").strip()
        ident = ("-c", "user.name=crewbots", "-c", "user.email=crewbots@localhost")
        tip = git(*ident, "commit-tree", tree, "-p", head, input=message).strip()
        commit = git(*ident, "commit-tree", tree, "-p", checkout.base, input=message).strip()
        if not on_branch(checkout):
            raise worktree.GitError("HEAD left the task branch; merge stopped")
        index_path, lock = index_lock_path(checkout.repo)
        nonce = (before_lock(commit, tip) if before_lock else None) or new_nonce()
        try:
            take_lock(lock, nonce)
        except OSError as e:
            raise worktree.GitError(f"cannot lock the Git index: {e}") from None
        new_index = None
        try:
            fd, new_index = tempfile.mkstemp(dir=lock.parent, prefix=temp_prefix(INDEX_TEMP, nonce))
            os.close(fd)
            if before_refs:
                before_refs(commit, tip)
            shutil.copyfile(index, new_index)
            if not on_branch(checkout):
                raise worktree.GitError("HEAD left the task branch; merge stopped")
            git("update-ref", "--stdin", input=(f"start\nupdate refs/heads/{checkout.branch} {tip} {head}\n"
                                                f"update {ref} {commit} {checkout.base}\nprepare\ncommit\n"))
            switched = False
            try:
                switch_head(checkout.repo, checkout.branch, checkout.original_branch)
                switched = True
                os.replace(new_index, index_path)
            except (OSError, worktree.GitError) as e:
                try:
                    current = worktree._git(checkout.repo, "symbolic-ref", "--short", "HEAD")
                    if switched and current not in (checkout.branch, checkout.original_branch):
                        raise worktree.GitError("HEAD changed during merge; leaving it alone")
                    if switched and current == checkout.original_branch:
                        switch_head(checkout.repo, checkout.original_branch, checkout.branch)
                    git("update-ref", "--stdin", input=(f"start\nupdate refs/heads/{checkout.branch} {head} {tip}\n"
                                                        f"update {ref} {checkout.base} {commit}\nprepare\ncommit\n"))
                except (OSError, worktree.GitError) as rollback:
                    raise worktree.GitError(f"merge failed: {e}; rollback also failed: {rollback}") from None
                raise worktree.GitError(f"merge failed and was rolled back: {e}") from None
        except OSError as e:
            raise worktree.GitError(f"cannot prepare the Git index: {e}") from None
        finally:
            failure = sys.exc_info()[1]
            try:
                if new_index:
                    Path(new_index).unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).exception("Could not remove temporary index %s", new_index)
            finally:
                released = clear_interrupted_lock(lock, nonce)
                if failure and not released:
                    raise worktree.GitError(f"{failure}; {lock_problem(lock, nonce)}") from None
        try:
            worktree._git(checkout.repo, "update-ref", "-d", f"refs/heads/{checkout.branch}", tip)
        except worktree.GitError:
            logging.getLogger(__name__).exception("Could not delete merged branch %s", checkout.branch)
        return commit, tip


def recover_merge(checkout: BranchCheckout, commit: str, tip: str, marker: str | None, mark_lock) -> bool:
    """Complete the HEAD and index steps after the base ref reached the recorded commit."""
    try:
        current = worktree._git(checkout.repo, "symbolic-ref", "--short", "HEAD")
    except worktree.GitError:
        current = None
    index, lock = index_lock_path(checkout.repo)
    if current != checkout.branch:
        return False
    if not clear_interrupted_lock(lock, marker):
        raise worktree.GitError(lock_problem(lock, marker))
    sweep_temp_files(checkout.repo, marker)
    try:
        branch_tip = worktree._git(checkout.repo, "rev-parse", "--verify", f"refs/heads/{checkout.branch}")
    except worktree.GitError:
        branch_tip = None
    if branch_tip is not None and branch_tip != tip:
        raise worktree.GitError("task branch changed during merge recovery")
    nonce = mark_lock()
    fd, new_index = tempfile.mkstemp(dir=lock.parent, prefix=temp_prefix(INDEX_TEMP, nonce))
    os.close(fd)
    try:
        env = dict(os.environ, GIT_INDEX_FILE=new_index)
        os.unlink(new_index)
        result = subprocess.run([paths.git(), "read-tree", commit], cwd=checkout.repo, env=env,
                                capture_output=True, text=True)
        if result.returncode:
            raise worktree.GitError(result.stderr.strip() or "could not rebuild the index")
        take_lock(lock, nonce)
        try:
            if worktree._git(checkout.repo, "rev-parse", f"refs/heads/{checkout.original_branch}") != commit:
                raise worktree.GitError("base branch moved during merge recovery")
            switch_head(checkout.repo, checkout.branch, checkout.original_branch)
            os.replace(new_index, index)
        finally:
            clear_interrupted_lock(lock, nonce)
    finally:
        Path(new_index).unlink(missing_ok=True)
    return True


def leave_merged_branch(checkout: BranchCheckout) -> None:
    """A merged task whose checkout is still on its branch: a plain switch, which refuses to overwrite local changes."""
    result = subprocess.run([paths.git(), "switch", "--no-overwrite-ignore", checkout.original_branch], cwd=checkout.repo,
                            capture_output=True, text=True)
    if result.returncode:
        raise worktree.GitError(result.stderr.strip() or result.stdout.strip() or "git switch failed")


def drop(checkout: BranchCheckout, task_id: str) -> None:
    """Save work on the task branch and return to the original branch."""
    if (checkout.repo / "Temp" / "UnityLockfile").exists():
        raise worktree.GitError(f"{checkout.repo} has a Unity lockfile; cannot switch branches")
    if worktree._git(checkout.repo, "symbolic-ref", "--short", "HEAD") != checkout.branch:
        raise worktree.GitError("HEAD is not on the task branch; leaving the checkout unchanged")
    wt = worktree.Worktree(checkout.repo, checkout.repo, checkout.branch, checkout.base)
    worktree.commit(wt, f"Uncommitted work from {task_id}, saved on withdrawal")
    if (checkout.repo / "Temp" / "UnityLockfile").exists():
        raise worktree.GitError(f"{checkout.repo} has a Unity lockfile; cannot switch branches")
    if not on_branch(checkout):
        raise worktree.GitError("HEAD left the task branch; leaving the checkout unchanged")
    worktree._git(checkout.repo, "switch", "--no-overwrite-ignore", checkout.original_branch)
