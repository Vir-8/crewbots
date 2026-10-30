"""Where the crew hears that something happened, detected cheaply, with no model call:

- `commit` and `branch`: new commits, or another branch checked out, in the user's workspaces (the bots' folders,
  and the repositories their work came from);
- `file`: files added, changed or removed in a folder a checklist watches (`Watch: ~/Documents/specs`);
- `review_requested`, `ci_failed` and `ci_passed`: from the GitHub poll the scheduler already makes;
- `thread_done`: a thread finishing, which the runner reports itself.

The first look at anything only records how it is: what was already there isn't news. Events go in the store
(`crew_events`), where check-ins read them and standing intents wait for them.
"""

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from crewbots import paths, worktree
from crewbots.github import Snapshot

KINDS = ("commit", "branch", "file", "review_requested", "ci_failed", "ci_passed", "thread_done")
MAX_FILES = 2000  # of a watched folder, two levels deep
MAX_LISTED = 10  # paths or commits named in one event


@dataclass(frozen=True)
class CrewEvent:
    kind: str
    subject: str  # what it's about: a repository, a folder, a pull request or a task
    text: str


class Sources:
    """What the user's workspaces, watched folders and pull requests looked like last time, to tell what's new."""

    def __init__(self) -> None:
        self._heads: dict[str, tuple[str, str]] = {}  # repository → (branch, head commit)
        self._files: dict[str, dict[str, float]] = {}  # folder → path → mtime
        self._prs: tuple[set[str], dict[str, str | None]] | None = None  # (asked, mine → checks)

    def git(self, repos: list[Path]) -> list[CrewEvent]:
        events = []
        for repo in repos:
            try:
                head = _git(repo, "rev-parse", "HEAD")
                branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")  # HEAD itself when detached
            except (subprocess.SubprocessError, OSError):
                continue
            now, before = (branch, head), self._heads.get(str(repo))
            self._heads[str(repo)] = now
            if before is None or before == now:
                continue
            if before[0] != now[0]:
                events.append(CrewEvent("branch", str(repo), f"{repo.name}: switched from {before[0]} to {now[0]}"))
            else:
                log = subprocess.run([paths.git(), "-C", str(repo), "log", "--format=%h %s", f"-{MAX_LISTED}",
                                      f"{before[1]}..{now[1]}"], capture_output=True, text=True, timeout=10).stdout
                commits = log.strip().splitlines() or [f"{now[1][:7]} (history rewritten)"]
                events.append(CrewEvent("commit", str(repo), f"{repo.name} on {now[0]}: " + "; ".join(commits)))
        return events

    def folders(self, folders: list[Path]) -> list[CrewEvent]:
        events = []
        for folder in folders:
            now = _mtimes(folder)
            before = self._files.get(str(folder))
            self._files[str(folder)] = now
            if before is None or before == now:
                continue
            added = sorted(set(now) - set(before))
            removed = sorted(set(before) - set(now))
            changed = sorted(p for p in set(now) & set(before) if now[p] != before[p])
            parts = [f"{label} {', '.join(paths[:MAX_LISTED])}" + (" …" if len(paths) > MAX_LISTED else "")
                     for label, paths in (("added", added), ("changed", changed), ("removed", removed)) if paths]
            events.append(CrewEvent("file", str(folder), f"{folder.name}: " + "; ".join(parts)))
        return events

    def github(self, snapshot: Snapshot) -> list[CrewEvent]:
        """New review requests, and the user's pull requests whose checks just failed or passed."""
        asked = {pr.key for pr in snapshot.review_requested}
        mine = {pr.key: pr.checks for pr in snapshot.mine}
        before = self._prs
        self._prs = (asked, mine)
        if before is None:
            return []
        events = [CrewEvent("review_requested", pr.key, f"{pr.author} asked you to review {pr.repo}#{pr.number}: "
                                                         f"{pr.title}") for pr in snapshot.review_requested
                  if pr.key not in before[0]]
        for pr in snapshot.mine:
            was = before[1].get(pr.key)
            if pr.checks in ("FAILURE", "ERROR") and was not in ("FAILURE", "ERROR"):
                events.append(CrewEvent("ci_failed", pr.key, f"checks failed on {pr.repo}#{pr.number}: {pr.title}"))
            elif pr.checks == "SUCCESS" and was not in (None, "SUCCESS"):
                events.append(CrewEvent("ci_passed", pr.key, f"checks passed on {pr.repo}#{pr.number}: {pr.title}"))
        return events


def repos(folders: list[Path | None]) -> list[Path]:
    """The git repositories among `folders`, each once, leaving out crewbots' own worktrees."""
    seen, out = set(), []
    for folder in folders:
        if folder is None:
            continue
        folder = Path(folder).expanduser()
        if str(folder) in seen or "worktrees" in folder.parts or not worktree.is_repo(folder):
            continue
        seen.add(str(folder))
        out.append(folder)
    return out


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([paths.git(), "-C", str(repo), *args], capture_output=True, text=True, timeout=10,
                          check=True).stdout.strip()


def _mtimes(folder: Path) -> dict[str, float]:
    found: dict[str, float] = {}
    started = time.monotonic()
    for root, dirs, files in os.walk(folder):
        depth = len(Path(root).relative_to(folder).parts)
        dirs[:] = [d for d in dirs if not d.startswith(".")] if depth < 2 else []
        for name in files:
            if name.startswith(".") or len(found) >= MAX_FILES:
                continue
            path = Path(root) / name
            try:
                found[str(path.relative_to(folder))] = path.stat().st_mtime
            except OSError:
                continue
        if len(found) >= MAX_FILES or time.monotonic() - started > 2:
            break
    return found
