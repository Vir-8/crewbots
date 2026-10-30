"""Work done in a project's folder itself (projects.py `in_place`) is committed there once it's approved, where a
worktree's work would be merged: the lead proposes it with `propose_merge` as usual, and crewbots commits just the files
the work handed back as its output, with their Unity .meta files and those of new folders they're in, on whatever
branch the folder is on, which must still be the one it was on when the work began. Other work in progress there, the
user's or another engineer's, stays uncommitted. The commit shows on the Landed page as a merge does, and the chief
proposes the push. Like a merge, it runs under merge_status.MERGE_LOCK, which its caller holds."""

import shlex
from pathlib import Path

from crewbots import comments, worktree
from crewbots.backends import Access
from crewbots.runner.common import MergeError
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
        return sorted({path for state, path in self._output_status(task, repo, output) if state != "!!"})

    def _ignored_output(self, task: Task, repo: Path) -> list[str]:
        """What the task handed back as its output that git ignores in this project, so that no commit of its holds
        it: whether to track it is the user's call."""
        return sorted({path for state, path in self._output_status(task, repo) if state == "!!"})

    def _nothing_to_commit(self, task: Task, repo: Path) -> str:
        if ignored := self._ignored_output(task, repo):
            return (f"git ignores what {task.id} handed back as its output in this project ({', '.join(ignored)}), so "
                    "there's nothing of its to commit. Whether to track it is the user's call: say so in your report, "
                    "and close the task with drop_task, which leaves its files as they are")
        return (f"{task.id} worked in its project's folder itself, and handed back none of the files it changed as its "
                "output, so there's nothing of its to commit: have it hand back again listing them")

    def _output_status(self, task: Task, repo: Path, output: list[str] | None = None) -> list[tuple[str, str]]:
        """`git status` of what the task handed back as its output, ignored files included (as `!!`)."""
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
        listed = worktree._git(repo, "status", "--porcelain=v1", "-z", "-uall", "--ignored", "--", *specs, strip=False)
        entries, found = listed.split("\0"), []
        while entries:
            entry = entries.pop(0)
            if len(entry) > 3:
                found.append((entry[:2], entry[3:]))
                if entry[0] in "RC" and entries:  # a rename or copy names where it came from next
                    found.append((entry[:2], entries.pop(0)))
        return found

    def _work_diff(self, task: Task, in_place: Path | None, files: list[str]) -> str:
        """What a piece of code work changes: its worktree's whole diff, or in place, its own files'."""
        if task.branch and task.worktree_path and Path(task.worktree_path).is_dir():
            return worktree.changes(worktree.Worktree(Path(task.repo), Path(task.worktree_path), task.branch, task.base))
        return self._in_place_diff(in_place, files) if in_place and files else ""

    def _in_place_diff(self, repo: Path, files: list[str]) -> str:
        """What `files` change, from the folder's last commit, without touching the index others share."""
        if not files:
            return ""
        listed = worktree._git(repo, "status", "--porcelain=v1", "-z", "-uall", "--", *files, strip=False).split("\0")
        new = [entry[3:] for entry in listed if entry.startswith("?? ")]
        tracked = [f for f in files if f not in new]
        diff = worktree._git(repo, "diff", "HEAD", "--", *tracked, strip=False) if tracked else ""
        return diff + "".join(comments.added(repo / name, name) for name in new)

    def _check_renames(self, repo: Path, files: list[str]) -> None:
        """Refuse a staged rename with only one side handed back: git can't tell it from someone else's delete and add."""
        listed = worktree._git(repo, "diff", "--cached", "-M", "--name-status", "-z", strip=False).split("\0")
        entries = iter(listed)
        for state in entries:
            if state[:1] in ("R", "C"):
                old, new = next(entries), next(entries)
                if state[0] == "R" and (old in files) != (new in files):
                    missing = new if old in files else old
                    raise MergeError(f"{missing} is the other side of a staged rename of {old if missing == new else new}, "
                                     f"but the Output doesn't list it, so nothing was committed: list both {old} and "
                                     f"{new} in the handback's Output, then merge again")
            elif state:
                next(entries)

    def _commit_in_place(self, work: Task, repo: Path, message: str, *, forced: bool, automatic: bool) -> Task:
        files = self._in_place_files(work, repo)
        if not files:
            raise MergeError(self._nothing_to_commit(work, repo))
        self._check_renames(repo, files)
        message = worktree.final_message(message)
        base = worktree._git(repo, "rev-parse", "HEAD")
        into = worktree._git(repo, "symbolic-ref", "--short", "HEAD")
        began = self.store.last_event(work.id, ("in_place",))
        if began and began.data.get("branch") != into:
            raise MergeError(f"{repo} is on {into} now, not {began.data.get('branch')} as when {work.id} began, so nothing "
                             "was committed: switch it back, then merge again")
        staged = set(worktree._git(repo, "ls-files", "-z", "--", *files, strip=False).split("\0"))
        ignored = set(worktree._git(repo, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--",
                                    *files, strip=False).split("\0"))
        present = [f for f in files if f in staged or ((repo / f).exists() and f not in ignored)]
        git_dir = Path(worktree._git(repo, "rev-parse", "--absolute-git-dir"))
        if (git_dir / "index.lock").exists():
            raise MergeError(f"{git_dir / 'index.lock'} is held by another git process, so nothing was committed: "
                             "wait for it to finish, then merge again")
        index = git_dir / f"crewbots-index-{work.id}"
        env = {"GIT_INDEX_FILE": str(index)}
        try:
            worktree._git(repo, "read-tree", "HEAD", env=env)
            if present:
                worktree._git(repo, "add", "--all", "--force", "--", *present, env=env)
            if gone := [f for f in files if f not in present]:
                worktree._git(repo, "update-index", "--force-remove", "--", *gone, env=env)
            worktree._git(repo, *worktree.NO_HOOKS, "commit", "-q", "--no-verify", "--cleanup=whitespace", "-m", message,
                          env=env)
        finally:
            index.unlink(missing_ok=True)
        worktree.verify_message(repo, "HEAD", message)
        commit = worktree._git(repo, "rev-parse", "HEAD")
        task = self._set(work.id, merge_state="merged", repo=str(repo), base=base, merged_commit=commit,
                         merged_tip=commit)
        data = {"commit": commit, "into": str(repo), "branch": into, "files": files,
                "ignored": self._ignored_output(work, repo), "forced": forced, "automatic": automatic}
        try:
            worktree._git(repo, "reset", "-q", "--", *files)
        except worktree.GitError as error:
            data["index"] = (f"commit {commit} landed, but the index for these files wasn't synced ({error}): "
                             f"run {shlex.join(['git', 'reset', '-q', '--', *files])} once the lock clears")
        self._record(work.id, kind="merged", data=data)
        return task
