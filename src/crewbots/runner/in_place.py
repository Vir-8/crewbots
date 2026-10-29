"""Work done in a project's folder itself (projects.py `in_place`) is committed there once it's approved, where a
worktree's work would be merged: the lead proposes it with `propose_merge` as usual, and crewbots commits just the files
the work handed back as its output, with their Unity .meta files and those of new folders they're in, on whatever
branch the folder is on. Other work in progress there, the user's or another engineer's, stays uncommitted. The commit
shows on the Landed page as a merge does, and the chief proposes the push."""

from pathlib import Path

from crewbots import worktree
from crewbots.backends import Access
from crewbots.runner.common import MergeError
from crewbots.runner.merge_status import _MERGE_LOCK
from crewbots.store import Task


class InPlace:
    """The part of Runner that commits work done in a project's folder itself."""

    def _in_place_repo(self, task: Task) -> Path | None:
        """The git repository a writing task worked in as it is, with no worktree of its own."""
        if task.worktree_path or task.kind != "thread" or task.review_of or not task.workspace:
            return None
        bot = self.bot_for(task.bot)
        folder = Path(task.workspace).expanduser()
        if bot is None or bot.access is Access.READ_ONLY or not folder.is_dir() or not worktree.is_repo(folder):
            return None
        return Path(worktree._git(folder, "rev-parse", "--show-toplevel"))

    def _in_place_files(self, task: Task, repo: Path, output: list[str] | None = None) -> list[str]:
        """The changed files among those the task handed back as its output (paths in the repository, or files and
        folders in it), with their .meta files and those of the folders they're in."""
        if output is None:
            handoff = self.store.latest_handoff(task.id)
            output = handoff.output if handoff else []
        specs: list[str] = []
        for item in output:
            path = Path(item.strip()).expanduser()
            try:
                rel = (path if path.is_absolute() else repo / path).resolve().relative_to(repo.resolve())
            except (ValueError, OSError):
                continue  # not a path in the repository: a branch, a link, a folder elsewhere
            if rel.parts:
                specs += [str(rel), f"{rel}.meta", *(f"{p}.meta" for p in rel.parents if p.parts)]
        if not specs:
            return []
        listed = worktree._git(repo, "status", "--porcelain=v1", "-z", "-uall", "--", *specs, strip=False)
        entries, changed = listed.split("\0"), set()
        while entries:
            entry = entries.pop(0)
            if len(entry) > 3:
                changed.add(entry[3:])
                if entry[0] in "RC" and entries:  # a rename or copy names where it came from next
                    changed.add(entries.pop(0))
        return sorted(changed)

    def _commit_in_place(self, work: Task, repo: Path, message: str, *, forced: bool, automatic: bool) -> Task:
        files = self._in_place_files(work, repo)
        if not files:
            raise MergeError(f"{work.id} handed back none of the files it changed as its output, so there's nothing of "
                             "its to commit: have it hand back again listing them")
        message = worktree.final_message(message)
        with _MERGE_LOCK:
            base = worktree._git(repo, "rev-parse", "HEAD")
            into = worktree._git(repo, "symbolic-ref", "--short", "HEAD")
            worktree._git(repo, "add", "--all", "--", *files)
            worktree._git(repo, *worktree.NO_HOOKS, "commit", "-q", "--no-verify", "--cleanup=whitespace", "-m",
                          message, "--", *files)  # just these, whatever else is staged
            worktree.verify_message(repo, "HEAD", message)
            commit = worktree._git(repo, "rev-parse", "HEAD")
        task = self._set(work.id, merge_state="merged", repo=str(repo), base=base, merged_commit=commit,
                         merged_tip=commit)
        self._record(work.id, kind="merged", data={"commit": commit, "into": str(repo), "branch": into, "files": files,
                                                   "forced": forced, "automatic": automatic})
        return task
