"""Code review, apart from the always-on crew: the pull requests waiting on you, and private reviews of them.

A review isn't one of your bots' work. Each of the models you choose (`reviewers:` in `reviews.yaml`) reviews the pull
request on its own (`REVIEWER`); then the first of them, as the lead (`LEAD_REVIEWER`), checks their findings against
the code, merges them, drafts the line comments and writes the one review you read and talk to. The reviewers'
own threads are there if you want them.

A reviewer is told only which pull request, and what you'd like looked at. Everything else it asks for: the pull
request's description, files, checks, linked issues and discussion (`pr_overview`), its diff (`pr_diff`), and the
code itself, the whole repository checked out at the pull request's head:

- from your local copy, when one is mapped (`repos:` in `reviews.yaml`), in a worktree of crewbots';
- otherwise from crewbots' own clone of the repository (blobless: file contents come only for what's checked out),
  so a later pull request of the same repository takes seconds;
- and only if that can't be had, the changed files fetched from GitHub, with tools to read the rest there.

Reviewers read and search the code themselves, and the web for anything outside the repository: GitHub's API is
used for the pull request itself, not for browsing code, since its code search allows ten queries a minute. A review
is static: nothing is run. Everything is read with your own `gh` and git logins, and nothing goes to GitHub until you
submit a review yourself.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from crewbots.github import GitHubError, _gh

REPOS_FILE = "reviews.yaml"  # in the crew's folder: {repos: {owner/name: local checkout}, reviewers: [...]}
WORKTREES = Path.home() / ".crewbots" / "worktrees" / "reviews"
SNAPSHOTS = Path.home() / ".crewbots" / "reviews"  # a pull request's changed files, fetched from GitHub
# In there: repos/, crewbots' own blobless clone of each repository it reviews, and checkouts/, pull requests' heads.
KEEP_CHECKOUTS = 6  # per repository; older ones are removed (each is a whole working tree)
REMOTE = "https://github.com/{repo}.git"
DIFF_PART_CHARS = 40_000  # pr_diff hands the diff over in parts this size, or a file at a time
SNAPSHOT_FILES = 200
FILE_BYTES = 400_000  # a changed file bigger than this is left out of the snapshot; the reviewer can still read it
FETCH_BATCH = 40  # files per GraphQL query
READ_LINES = 400
DEFAULT_REVIEWERS = [{"backend": "claude", "model": "sonnet", "effort": "high"},
                     {"backend": "codex", "model": None, "effort": "high"}]  # None: Codex's own default model
MAX_REVIEWERS = 4
ISSUE_CHARS = 2_500
DISCUSSION_CHARS = 5_000
REVIEWER_PREFIX = "reviewer:"  # a reviewer's name: reviewer:<backend>:<model>:<effort>
HOW_TO_REVIEW = """Your working directory is the repository, checked out at the pull request's head. Start with
`pr_overview` (what the pull request is for, its files, checks, linked issues and discussion) and `pr_diff` (its
changes; a file at a time, or in parts, if it's long). Then read around the change in the checkout: the callers, the
types, the tests, how the touched code is used elsewhere. Check claims against the code rather than speculating. For
anything outside the repository, such as a library's behaviour or its documentation, search the web.

Review statically, by reading. Never run tests, builds, linters, type checks, package managers, servers or the app,
and never install anything: a review reads code, it doesn't execute it. If you can run commands, run only ones that
read and search (rg, grep, ls, cat, git log, git show). Never change files, and never post, comment, approve or submit
anything anywhere.

Find what's actually wrong: bugs, regressions, broken edge cases, security problems, missing or misleading tests, and
changes that don't do what the pull request says. No style nits a linter would catch, and no restating the diff."""

COMMENT_WORDS = 70  # past this, a reviewer's draft is sent back to be shortened (code blocks don't count)
LINE_COMMENT = """A line comment is for the pull request's author, and short: one to three plain sentences, under
50 words, saying what's wrong and what to change, so they know at once what's expected. Write it as a request or a
question ("This skips the last row when `rows` is empty; start the range at 0?"), not an essay: no background, no
walkthrough of your reasoning, no severity labels, headings or lists. A one-line fix can be a ```suggestion block
holding the line's replacement. The why and the evidence belong in your reply, not on the line."""

REVIEWER = f"""You are one of the reviewers of a pull request. You review it on your own; a lead reviewer then puts
your findings together with the others' into the review the person reads.

{HOW_TO_REVIEW}

Reply with your findings, most serious first, each with:
- P0 (must fix), P1 (should fix) or P2 (worth fixing)
- where: path:line, the line's number in the pull request's version of the file, or path:line (removed) for a removed
  line by its old number
- the problem, and the input or state that goes wrong
- the comment you'd leave on that line

{LINE_COMMENT}

Then a line on the change's overall risk, and what you couldn't check. Nothing else."""

LEAD_REVIEWER = f"""You lead a private review of one pull request, for the person you're talking to. Other reviewers,
on other models, review it on their own first, and their findings are your first message. You put them together.

{HOW_TO_REVIEW}

1. Check every finding against the code: reviewers are sometimes wrong. Drop what doesn't hold up.
2. Merge findings that are the same problem, however differently worded or on whichever nearby line.
3. If a reviewer failed or missed part of the change, review that part yourself.
4. For each finding that belongs on a line, draft one comment with `draft_comment(path, line, body)`: one per problem,
   never twice. `line` is its number in the pull request's version of the file (`side: LEFT` and its old number for a
   removed line), a line the diff shows. Reviewers' suggested comments are often too long: rewrite them. Drafts are
   private: the person reads, edits or deletes them, and decides whether to submit a review.

{LINE_COMMENT}

Then reply with the review:
1. One line: no blocking issues, or how many issues there are.
2. The findings, most serious first, each as P0/P1/P2 with path:line and the problem, and which reviewers found it.
3. Overall impact: what this change affects, and how risky it is.
4. What couldn't be checked, and why.

Afterwards, answer follow-up questions about the pull request plainly and briefly."""
INBOX_TTL_S = 60.0
DETAIL_TTL_S = 120.0
VIEW_DIFF_CHARS = 800_000
BODY_CHARS = 6_000
REPO = re.compile(r"[\w.-]+/[\w.-]+")

# What's waiting on you, one search a list, each asked on its own and all at once: together in one query they took
# GitHub 7 to 8 seconds for a busy account, close to its GraphQL time limit (a 504), and in parallel the slowest
# takes about half that. Your teams' list, often the longest, is only asked for when you open it.
SEARCHES = {
    "assigned": "is:pr is:open archived:false assignee:@me -author:@me",
    "direct": "is:pr is:open archived:false user-review-requested:@me -author:@me",
    "team": "is:pr is:open archived:false review-requested:@me -user-review-requested:@me -author:@me",
    "mine": "is:pr is:open archived:false author:@me",
}
INBOX_LISTS = ("assigned", "direct", "mine")  # what the app asks for first; "team" when you open it
PR_FIELDS = """
fragment pr on PullRequest {
  number title url isDraft createdAt updatedAt additions deletions changedFiles reviewDecision headRefName baseRefName
  author { login avatarUrl } repository { nameWithOwner }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
}
"""


def inbox_query(keys) -> str:
    searches = "\n".join(f'  {k}: search(query: "{SEARCHES[k]}", type: ISSUE, first: 50) {{ nodes {{ ...pr }} }}'
                          for k in keys)
    return f"query {{\n{searches}\n}}\n{PR_FIELDS}"

DETAIL_FIELDS = ("number,title,body,url,state,isDraft,author,headRefName,baseRefName,headRefOid,baseRefOid,additions,deletions,"
                 "changedFiles,files,reviewDecision,createdAt,updatedAt,statusCheckRollup,labels,reviews,comments,"
                 "closingIssuesReferences,assignees,reviewRequests")
ISSUE_REF = re.compile(r"(?<![\w/])#(\d{1,7})\b")


class ReviewError(ValueError):
    pass


def _people(raw: dict[str, Any]) -> list[str]:
    """Who a comment might @mention: the author first, then assignees, requested reviewers, and whoever has reviewed
    or commented, each once."""
    logins = [(raw.get("author") or {}).get("login")]
    logins += [a.get("login") for a in raw.get("assignees") or []]
    logins += [r.get("login") or r.get("slug") for r in raw.get("reviewRequests") or []]  # a user, or a team
    logins += [(x.get("author") or {}).get("login") for x in [*(raw.get("reviews") or []), *(raw.get("comments") or [])]]
    return list(dict.fromkeys(str(login) for login in logins if login))


@dataclass
class _Cached:
    at: float
    value: Any


class Reviews:
    def __init__(self, crew_home: Path, gh=_gh, worktrees: Path = WORKTREES, snapshots: Path = SNAPSHOTS,
                 remote: str = REMOTE):
        self.home = crew_home
        self.gh = gh
        self.worktrees = worktrees
        self.snapshots = snapshots
        self.clones = snapshots / "repos"
        self.checkouts = snapshots / "checkouts"
        self.remote = remote  # where a repository is cloned from: GitHub, or a folder in tests
        self._cache: dict[str, _Cached] = {}
        self._loading: dict[str, threading.Event] = {}  # keys being fetched now, set when each is done
        self._lock = threading.Lock()
        self._repo_locks: dict[str, threading.Lock] = {}  # one git operation on a repository's clone at a time

    # What's waiting.

    def inbox(self, lists=INBOX_LISTS, fresh: bool = False) -> tuple[dict[str, list[dict[str, Any]]], str | None]:
        """The asked-for lists of pull requests, and GitHub's error if one couldn't be read (that list is then the
        last good one, or empty)."""
        def ask(key: str) -> list[dict[str, Any]]:
            data = json.loads(self.gh(["api", "graphql", "-f", f"query={inbox_query([key])}"])).get("data") or {}
            return [_summary(n) for n in (data.get(key) or {}).get("nodes") or [] if n and "number" in n]

        def one(key: str) -> tuple[list[dict[str, Any]], str | None]:
            try:
                return self._kept(f"inbox:{key}", INBOX_TTL_S, lambda: ask(key), fresh), None
            except GitHubError as e:  # the last good list, if there is one, stays up
                hit = self._cache.get(f"inbox:{key}")
                return (hit.value if hit else []), str(e)

        with ThreadPoolExecutor(len(lists)) as pool:
            found = dict(zip(lists, pool.map(one, lists), strict=True))
        return {k: v for k, (v, _) in found.items()}, next((e for _, e in found.values() if e), None)

    def pull(self, repo: str, number: int) -> dict[str, Any]:
        _check(repo)

        def load() -> dict[str, Any]:
            raw = json.loads(self.gh(["pr", "view", str(number), "--repo", repo, "--json", DETAIL_FIELDS]))
            checks = [c for c in raw.get("statusCheckRollup") or [] if isinstance(c, dict)]
            states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in checks]
            failing = [c.get("name") or c.get("context") or "check" for c, s in zip(checks, states, strict=True)
                       if s in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED")]
            said = [{"author": (r.get("author") or {}).get("login", ""), "kind": f"review ({r.get('state', '').lower()})",
                     "body": r.get("body") or ""} for r in raw.get("reviews") or [] if (r.get("body") or "").strip()]
            said += [{"author": (c.get("author") or {}).get("login", ""), "kind": "comment", "body": c.get("body") or ""}
                     for c in raw.get("comments") or [] if (c.get("body") or "").strip()]
            return {
                "repo": repo, "number": raw["number"], "title": raw["title"], "body": raw.get("body") or "",
                "url": raw["url"], "state": raw.get("state"), "draft": raw.get("isDraft", False),
                "author": (raw.get("author") or {}).get("login", ""), "head": raw.get("headRefName", ""),
                "base": raw.get("baseRefName", ""), "head_sha": raw.get("headRefOid", ""),
                "base_sha": raw.get("baseRefOid", ""),
                "additions": raw.get("additions", 0), "deletions": raw.get("deletions", 0),
                "changed_files": raw.get("changedFiles", 0), "review_decision": raw.get("reviewDecision"),
                "created_at": raw.get("createdAt"), "updated_at": raw.get("updatedAt"),
                "labels": [label.get("name", "") for label in raw.get("labels") or []],
                "files": [{"path": f["path"], "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}
                          for f in raw.get("files") or []],
                "checks": {"total": len(states), "passed": sum(s in ("SUCCESS", "NEUTRAL", "SKIPPED") for s in states),
                           "failed": len(failing)},
                "failing": failing,
                "discussion": said,
                "people": _people(raw),
                "issues": sorted({int(i["number"]) for i in raw.get("closingIssuesReferences") or [] if "number" in i}
                                 | {int(n) for n in ISSUE_REF.findall(f"{raw['title']}\n{raw.get('body') or ''}")})[:3],
                "checkout": str(self.checkout_of(repo)) if self.checkout_of(repo) else None,
            }
        return self._kept(f"pull:{repo}#{number}", DETAIL_TTL_S, load)

    def diff(self, repo: str, number: int, limit: int = VIEW_DIFF_CHARS) -> str:
        _check(repo)
        text = self._kept(f"diff:{repo}#{number}", DETAIL_TTL_S,
                          lambda: self.gh(["pr", "diff", str(number), "--repo", repo]))
        if len(text) > limit:
            return text[:limit] + f"\n… (the diff goes on for {len(text) - limit:,} more characters)"
        return text

    # Review comments, and submitting a review: the user's alone. No tool reaches these but `check_draft`.

    def check_draft(self, repo: str, number: int, path: str, line: int, side: str = "RIGHT") -> dict[str, Any]:
        """Where a comment can go: a line the diff shows, on that side. Raises ReviewError with where it can't."""
        _check(repo)
        if side not in ("RIGHT", "LEFT"):
            raise ReviewError("side is RIGHT, for the pull request's version, or LEFT, for a removed line")
        lines = commentable(self.diff(repo, number, limit=10**9))
        if path not in lines:
            raise ReviewError(f"{path} isn't one of this pull request's changed files")
        if line not in lines[path][side]:
            shown = sorted(lines[path][side])
            span = f"{shown[0]} to {shown[-1]}" if shown else "none"
            raise ReviewError(f"line {line} of {path} isn't in the diff on the {side} side (it shows {span}, in places); "
                              "comment on a line the diff shows, or put it in the summary")
        return {"path": path, "line": line, "side": side, "commit_sha": self.pull(repo, number)["head_sha"]}

    def submit(self, repo: str, number: int, event: str, body: str, comments: list[dict[str, Any]]) -> dict[str, Any]:
        """Posts the user's review to GitHub, with its line comments, as one review: what the app's Submit does. The
        only write to GitHub crewbots makes."""
        _check(repo)
        if event not in REVIEW_EVENTS:
            raise ReviewError("a review approves, requests changes, or comments")
        if event != "APPROVE" and not body.strip() and not comments:
            raise ReviewError("a review that doesn't approve needs a comment, or line comments")
        if event == "REQUEST_CHANGES" and not body.strip():
            raise ReviewError("requesting changes needs a comment saying what to change")
        payload = {"commit_id": self.pull(repo, number)["head_sha"], "event": event, "body": body,
                   "comments": [{"path": c["path"], "line": c["line"], "side": c["side"], "body": c["body"]}
                                for c in comments]}
        posted = json.loads(self.gh(["api", "-X", "POST", f"repos/{repo}/pulls/{number}/reviews", "--input", "-"],
                                    stdin=json.dumps(payload)))
        self._cache.pop(f"pull:{repo}#{number}", None)
        return {"url": posted.get("html_url", ""), "state": posted.get("state", ""), "comments": len(comments)}

    def image(self, repo: str, number: int, path: str, side: str) -> dict[str, str]:
        """An image the pull request changes, as it is at its head or on its base: base64, for the diff to show."""
        _check(repo)
        mime = IMAGE_TYPES.get(Path(path).suffix.lower())
        if mime is None:
            raise ReviewError(f"{path} isn't an image")
        pr = self.pull(repo, number)
        sha = pr["head_sha"] if side == "head" else pr.get("base_sha") or pr["base"]

        def load() -> str:
            found = json.loads(self.gh(["api", f"repos/{repo}/contents/{path}?ref={sha}"]))
            data = found.get("content") or ""
            if not data and found.get("sha"):  # over a megabyte: the contents API leaves it out
                if int(found.get("size") or 0) > IMAGE_BYTES:
                    raise ReviewError(f"{path} is too big to show here")
                data = json.loads(self.gh(["api", f"repos/{repo}/git/blobs/{found['sha']}"])).get("content") or ""
            return data.replace("\n", "")

        return {"mime": mime, "data": self._kept(f"image:{repo}@{sha}:{path}", DETAIL_TTL_S * 30, load)}

    # Reviewing.

    def request(self, repo: str, number: int, note: str = "") -> str:
        """What you asked for, as the lead's conversation shows it: the pull request, and what to look at."""
        url = self.pull(repo, number)["url"]
        return f"Review {url}" + (f"\n\n{note.strip()}" if note.strip() else "")

    def goal(self, repo: str, number: int, note: str, whole: bool) -> str:
        """A reviewer's first message: which pull request, what you'd like looked at, and where its code is. The rest
        it asks for with its tools, as it needs it."""
        pr = self.pull(repo, number)
        where = (f"Your working directory is the repository, checked out at the pull request's head ({pr['head_sha'][:12]})."
                 if whole else
                 f"Your working directory has only the files this pull request changes, as they are at its head "
                 f"({pr['head_sha'][:12]}): the repository couldn't be checked out. read_file and list_files read the "
                 "rest of it on GitHub; fetch only what a finding depends on.")
        asked = f"\n\nWhat I'd like looked at in particular: {note.strip()}" if note.strip() else ""
        return f"Review {pr['url']}.{asked}\n\n{where}"

    def checkout(self, repo: str, number: int) -> tuple[Path, bool]:
        """Where reviewers work, and whether it's the whole repository: your mapped copy's worktree, else crewbots'
        own clone's, else (a private repository git can't fetch, say) the changed files fetched from GitHub."""
        pr = self.pull(repo, number)
        ways = [self.worktree] if self.checkout_of(repo) else []
        for way in [*ways, self.cached_checkout]:
            try:
                return way(repo, number, pr["head_sha"]), True
            except ReviewError:
                continue
        return self.snapshot(repo, number, pr["head_sha"], [f["path"] for f in pr["files"]]), False

    def cached_checkout(self, repo: str, number: int, sha: str) -> Path:
        """The pull request's head, checked out from crewbots' own clone of the repository. The clone is blobless:
        the history, without file contents until a checkout needs them, so the first review of a big repository
        takes about a minute and a later pull request of it a few seconds."""
        _check(repo)
        target = self.checkouts / f"{repo.replace('/', '-')}-{number}-{sha[:10]}"
        if (target / ".git").exists():
            return target
        clone = self.clones / repo.replace("/", "__")
        with self._git_lock(repo):
            if not (clone / ".git").exists():
                clone.parent.mkdir(parents=True, exist_ok=True)
                self._git(["clone", "--filter=blob:none", "--no-checkout", "--quiet", self.remote.format(repo=repo),
                           str(clone)], repo, timeout=900)
            if self._git(["-C", str(clone), "cat-file", "-t", sha], repo, check=False).strip() != "commit":
                self._git(["-C", str(clone), "fetch", "--no-tags", "--quiet", "origin", f"pull/{number}/head"], repo)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._git(["-C", str(clone), "worktree", "add", "--detach", "--quiet", str(target), sha], repo,
                          timeout=900)
            except ReviewError:  # nothing half checked out is left to look finished next time
                self._git(["-C", str(clone), "worktree", "remove", "--force", str(target)], repo, check=False)
                shutil.rmtree(target, ignore_errors=True)
                raise
            self._prune(clone, repo)
        return target

    def is_snapshot(self, folder: str | None) -> bool:
        """Whether a review's folder holds only the changed files, so its reviewers read the rest on GitHub."""
        return bool(folder) and Path(folder).parent == self.snapshots

    def _prune(self, clone: Path, repo: str) -> None:
        mine = sorted(self.checkouts.glob(f"{repo.replace('/', '-')}-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in mine[KEEP_CHECKOUTS:]:
            self._git(["-C", str(clone), "worktree", "remove", "--force", str(old)], repo, check=False)

    def _git(self, args: list[str], repo: str, timeout: float = 600, check: bool = True) -> str:
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # a private repository without a login fails, never asks
        try:
            done = subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout, env=env,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ReviewError(f"getting {repo}'s code took too long") from None
        if check and done.returncode != 0:
            raise ReviewError(f"couldn't get {repo}'s code: {(done.stderr or done.stdout).strip().splitlines()[-1:]}")
        return done.stdout

    def _git_lock(self, repo: str) -> threading.Lock:
        with self._lock:
            return self._repo_locks.setdefault(repo, threading.Lock())

    # What a reviewer asks for about the pull request itself.

    def overview(self, repo: str, number: int) -> str:
        """`pr_overview`: what the pull request is for and where it stands, without its diff."""
        pr = self.pull(repo, number)
        checks = pr["checks"]
        lines = [f"# {pr['title']}", pr["url"],
                 f"{repo}#{number} by {pr['author']} · {pr['head']} → {pr['base']} · head {pr['head_sha'][:12]}",
                 f"{pr['changed_files']} files, +{pr['additions']} −{pr['deletions']} · checks: {checks['passed']} of "
                 f"{checks['total']} passed, {checks['failed']} failed"
                 + (f" ({', '.join(pr['failing'][:10])})" if pr["failing"] else ""),
                 "", "## Description", "", _clip(pr["body"], BODY_CHARS) if pr["body"].strip() else "(none)",
                 "", "## Files", "", *(f"- {f['path']} (+{f['additions']} −{f['deletions']})" for f in pr["files"][:300])]
        issues = [i for i in (self.issue(repo, n) for n in pr["issues"]) if i]
        if issues:
            lines += ["", "## Issues it refers to", *(f"\n### #{i['number']}: {i['title']}\n\n{_clip(i['body'], ISSUE_CHARS)}"
                                                      for i in issues)]
        if pr["discussion"]:
            said = "\n\n".join(f"**{d['author']}** ({d['kind']}): {_clip(d['body'], 800)}" for d in pr["discussion"])
            lines += ["", "## What's been said on it", "", _clip(said, DISCUSSION_CHARS)]
        return "\n".join(lines)

    def diff_part(self, repo: str, number: int, path: str | None = None, part: int = 1) -> str:
        """`pr_diff`: one file's diff, or the whole diff in parts of whole files, so a big one never floods a turn."""
        diff = self.diff(repo, number, limit=10**9)
        files = [f for f in re.split(r"(?m)^(?=diff --git )", diff) if f.strip()]
        if path:
            found = [f for f in files if re.match(rf"diff --git a/\S+ b/{re.escape(path)}\n", f)]
            if not found:
                raise ReviewError(f"{path} isn't one of this pull request's changed files")
            return _clip(found[0], DIFF_PART_CHARS * 2)
        parts: list[str] = []
        for f in files:
            if parts and len(parts[-1]) + len(f) <= DIFF_PART_CHARS:
                parts[-1] += f
            else:
                parts.append(f if len(f) <= DIFF_PART_CHARS else
                             _clip(f, DIFF_PART_CHARS) + "\n(this file's diff goes on: ask for it by its path)\n")
        if not parts:
            return "The diff is empty."
        if not 1 <= part <= len(parts):
            raise ReviewError(f"the diff has {len(parts)} part(s)")
        return (f"Part {part} of {len(parts)}" + (f"; ask for part {part + 1} next" if part < len(parts) else "")
                + f".\n\n{parts[part - 1]}")

    def snapshot(self, repo: str, number: int, sha: str, paths: list[str]) -> Path:
        """The changed files as they are at the pull request's head, in a folder of their own: real files the reviewer
        can read and search, without the rest of a repository that may be huge."""
        folder = self.snapshots / f"{repo.replace('/', '-')}-{number}-{sha[:10]}"
        if (folder / ".complete").exists():
            return folder
        folder.mkdir(parents=True, exist_ok=True)
        for batch in range(0, min(len(paths), SNAPSHOT_FILES), FETCH_BATCH):
            for path, text in self.blobs(repo, sha, paths[batch:batch + FETCH_BATCH]).items():
                if text is None or len(text) > FILE_BYTES:
                    continue  # removed, binary or huge: read_file still reaches it
                target = (folder / path).resolve()
                if folder.resolve() not in target.parents:
                    continue  # a path that would leave the folder
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
        (folder / ".complete").write_text(sha)
        return folder

    def blobs(self, repo: str, sha: str, paths: list[str]) -> dict[str, str | None]:
        """Several files' text at a commit, in one GraphQL query; None for one that's missing or binary."""
        owner, name = repo.split("/")
        fields = " ".join(f"f{i}: object(expression: {json.dumps(f'{sha}:{p}')}) {{ ... on Blob {{ text isBinary }} }}"
                          for i, p in enumerate(paths))
        query = f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {fields} }} }}"
        data = (json.loads(self.gh(["api", "graphql", "-f", f"query={query}"])).get("data") or {}).get("repository") or {}
        out = {}
        for i, path in enumerate(paths):
            blob = data.get(f"f{i}") or {}
            out[path] = None if blob.get("isBinary") or blob.get("text") is None else blob["text"]
        return out

    # What the reviewer can ask for, when it doesn't have your copy: GitHub, at the pull request's head, read-only.

    def read_file(self, repo: str, number: int, path: str, start: int = 1, end: int | None = None) -> str:
        sha = self.pull(repo, number)["head_sha"]
        text = self._kept(f"blob:{repo}@{sha}:{path}", DETAIL_TTL_S * 10, lambda: self.blobs(repo, sha, [path])[path])
        if text is None:
            return f"{path} isn't a text file at {sha[:12]} (it may not exist there, or be binary)."
        lines = text.splitlines()
        start = max(1, start)
        end = min(len(lines), end or start + READ_LINES - 1, start + READ_LINES - 1)
        shown = "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))
        more = f"\n… {len(lines) - end} more lines (ask for start={end + 1})" if end < len(lines) else ""
        return f"{path} at {sha[:12]}, lines {start}-{end} of {len(lines)}:\n{shown}{more}"

    def list_files(self, repo: str, number: int, path: str = "") -> str:
        sha = self.pull(repo, number)["head_sha"]
        owner, name = repo.split("/")
        expression = json.dumps(f"{sha}:{path.strip('/')}")
        query = (f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ "
                 f"object(expression: {expression}) {{ ... on Tree {{ entries {{ name type }} }} }} }} }}")
        data = json.loads(self.gh(["api", "graphql", "-f", f"query={query}"])).get("data") or {}
        entries = (((data.get("repository") or {}).get("object")) or {}).get("entries")
        if entries is None:
            return f"{path or '/'} isn't a folder at {sha[:12]}."
        return "\n".join(f"{e['name']}{'/' if e['type'] == 'tree' else ''}" for e in entries)

    def issue(self, repo: str, number: int) -> dict[str, Any] | None:
        def load() -> dict[str, Any] | None:
            try:
                raw = json.loads(self.gh(["issue", "view", str(number), "--repo", repo, "--json", "number,title,body"]))
            except (GitHubError, ValueError):
                return None  # a pull request, or gone
            return {"number": raw["number"], "title": raw.get("title", ""), "body": raw.get("body") or ""}
        return self._kept(f"issue:{repo}#{number}", DETAIL_TTL_S * 5, load)

    # Who reviews: models, side by side.

    def reviewers(self) -> list[dict[str, Any]]:
        data = self._config()
        return [_reviewer(r) for r in data.get("reviewers") or DEFAULT_REVIEWERS]

    def set_reviewers(self, reviewers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not 1 <= len(reviewers) <= MAX_REVIEWERS:
            raise ReviewError(f"choose between 1 and {MAX_REVIEWERS} reviewers")
        cleaned = [_reviewer(r) for r in reviewers]
        data = self._config()
        data["reviewers"] = cleaned
        self._save(data)
        return cleaned

    def worktree(self, repo: str, number: int, sha: str) -> Path:
        """The pull request's head, checked out from your local copy of the repo into a worktree of crewbots'."""
        checkout = self.checkout_of(repo)
        assert checkout
        path = self.worktrees / f"{repo.replace('/', '-')}-{number}-{sha[:10]}"
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        git = ["git", "-C", str(checkout)]
        for args in ([*git, "fetch", "--no-tags", "--quiet", "origin", f"pull/{number}/head"],
                     [*git, "worktree", "add", "--detach", "--quiet", str(path), sha]):
            done = subprocess.run(args, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
            if done.returncode != 0:
                raise ReviewError(f"couldn't check out {repo}#{number} in {checkout}: "
                                  f"{(done.stderr or done.stdout).strip().splitlines()[-1:]}")
        return path

    # Which repos have a local copy.

    def repos(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in (self._config().get("repos") or {}).items()}

    def _config(self) -> dict[str, Any]:
        path = self.home / REPOS_FILE
        return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}

    def _save(self, data: dict[str, Any]) -> None:
        (self.home / REPOS_FILE).write_text(yaml.safe_dump(data, sort_keys=False))

    def set_repo(self, repo: str, folder: str | None) -> dict[str, str]:
        _check(repo)
        repos = self.repos()
        if folder:
            path = Path(folder).expanduser()
            if not (path / ".git").exists():
                raise ReviewError(f"{path} isn't a git checkout")
            repos[repo] = str(path)
        else:
            repos.pop(repo, None)
        data = self._config()
        data["repos"] = repos
        self._save(data)
        self._cache = {k: v for k, v in self._cache.items() if not k.startswith(f"pull:{repo}#")}
        return repos

    def checkout_of(self, repo: str) -> Path | None:
        folder = self.repos().get(repo)
        return Path(folder).expanduser() if folder else None

    def _kept(self, key: str, ttl: float, load, fresh: bool = False):
        """GitHub's answer for `key`, kept: one older than `ttl` comes back at once while a new one is fetched behind
        it (a failed fetch leaves the old one), and only `fresh`, or nothing kept yet, waits for GitHub. Only one fetch
        per key runs at a time; a second ask waits for it."""
        hit = self._cache.get(key)
        if hit and not fresh:
            if time.monotonic() - hit.at >= ttl:
                threading.Thread(target=self._fetch, args=(key, load), daemon=True).start()
            return hit.value
        return self._fetch(key, load, wait=True)

    def _fetch(self, key: str, load, wait: bool = False):
        with self._lock:
            running = self._loading.get(key)
            if running is None:
                done = self._loading[key] = threading.Event()
        if running is not None:  # someone is already asking
            if not wait:
                return None
            running.wait()
            if key in self._cache:
                return self._cache[key].value
            return self._fetch(key, load, wait=True)  # theirs failed: ask ourselves, and raise what GitHub says
        try:
            value = load()
            self._cache[key] = _Cached(time.monotonic(), value)
            return value
        except Exception:
            if wait:
                raise
            return None  # behind a kept answer: that one stays
        finally:
            with self._lock:
                self._loading.pop(key, None)
            done.set()


def _summary(node: dict[str, Any]) -> dict[str, Any]:
    commit = ((node.get("commits") or {}).get("nodes") or [{}])[-1].get("commit") or {}
    return {
        "repo": node["repository"]["nameWithOwner"], "number": node["number"], "title": node.get("title", ""),
        "url": node.get("url", ""), "draft": node.get("isDraft", False),
        "author": (node.get("author") or {}).get("login", ""),
        "author_avatar": (node.get("author") or {}).get("avatarUrl"),
        "created_at": node.get("createdAt"), "updated_at": node.get("updatedAt"),
        "additions": node.get("additions", 0), "deletions": node.get("deletions", 0),
        "changed_files": node.get("changedFiles", 0), "review_decision": node.get("reviewDecision"),
        "checks": (commit.get("statusCheckRollup") or {}).get("state"),
    }


def _reviewer(raw: dict[str, Any]) -> dict[str, Any]:
    from crewbots.crew import EFFORTS

    backend = raw.get("backend")
    if backend not in ("claude", "codex"):
        raise ReviewError(f"a reviewer runs on claude or codex, not {backend!r}")
    effort = raw.get("effort") or None
    if effort is not None and effort not in EFFORTS:
        raise ReviewError(f"effort is one of {list(EFFORTS)}, not {effort!r}")
    model = str(raw["model"]).strip() if raw.get("model") else None
    if model and not re.fullmatch(r"[\w.\-\[\]]+", model):
        raise ReviewError(f"{model!r} isn't a model name")
    return {"backend": backend, "model": model, "effort": effort}


def reviewer_name(reviewer: dict[str, Any]) -> str:
    """A reviewer's name, which is also how to run it again: reviewer:<backend>:<model>:<effort>."""
    return f"{REVIEWER_PREFIX}{reviewer['backend']}:{reviewer['model'] or 'default'}:{reviewer['effort'] or 'default'}"


def reviewer_bot(name: str):
    """The agent a review runs as, from its name. It isn't one of the crew: no roster, no crew tools, only reading."""
    from crewbots.backends import Access
    from crewbots.crew import Bot

    if not name.startswith(REVIEWER_PREFIX):
        return None
    try:
        backend, model, effort = name.removeprefix(REVIEWER_PREFIX).split(":")
    except ValueError:
        return None
    return Bot(name=name, title="Reviewer", description="Reviews one pull request, privately.", instructions=REVIEWER,
               backend=backend, model=None if model == "default" else model, access=Access.READ_ONLY,
               worktree=False, effort=None if effort == "default" else effort,
               network=True)  # to search the web for what's outside the repository; it still can't change anything


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…"


def _check(repo: str) -> None:
    if not REPO.fullmatch(repo):
        raise ReviewError(f"{repo!r} isn't owner/name")


__all__ = ["Reviews", "ReviewError", "GitHubError"]


# The reviewer's tools, when it reads from GitHub: defined once, served in-process to Claude and over MCP to Codex.

REVIEW_EVENTS = {"APPROVE", "REQUEST_CHANGES", "COMMENT"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp", ".svg": "image/svg+xml", ".bmp": "image/bmp", ".ico": "image/x-icon"}
IMAGE_BYTES = 15_000_000


def commentable(diff: str) -> dict[str, dict[str, set[int]]]:
    """The lines a review comment can go on, file by file: what GitHub accepts, the lines the diff shows. RIGHT is the
    pull request's version of the file (added and unchanged lines, by their new numbers), LEFT the base's (removed
    and unchanged lines, by their old numbers)."""
    files: dict[str, dict[str, set[int]]] = {}
    path = old_path = None
    old = new = 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = old_path = None
        elif line.startswith("--- "):
            old_path = line[6:] if line.startswith("--- a/") else None
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else old_path
            if path:
                files.setdefault(path, {"RIGHT": set(), "LEFT": set()})
        elif line.startswith("@@"):
            hunk = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)", line)
            if hunk:
                old, new = int(hunk.group(1)), int(hunk.group(2))
        elif path is None:
            continue
        elif line.startswith("+"):
            files[path]["RIGHT"].add(new)
            new += 1
        elif line.startswith("-"):
            files[path]["LEFT"].add(old)
            old += 1
        elif line.startswith(" "):  # unchanged; git writes a blank one as a single space
            files[path]["RIGHT"].add(new)
            files[path]["LEFT"].add(old)
            old, new = old + 1, new + 1
    return files


def _review_tools(reads_github: bool = False, drafting: bool = True):
    """A review's tools. Every reviewer reads the pull request itself (`pr_overview`, `pr_diff`); the lead drafts the
    line comments; and only without a checkout of the repository do they read it on GitHub. None posts anything."""
    from crewbots.crew_tools import STRING, ToolDef, _object

    tools = [
        ToolDef("pr_overview", "The pull request itself: title, link, author, branches, description, its files with "
                "lines added and removed, its checks and which fail, the issues it refers to, and what's been said on "
                "it so far. Not its diff.", _object({}, [])),
        ToolDef("pr_diff", "The pull request's diff. path: one changed file's diff. Without a path, the whole diff in "
                "parts of about 40,000 characters, whole files each: part, from 1, says which.",
                _object({"path": STRING, "part": {"type": "integer"}}, [])),
    ]
    if drafting:
        tools.append(ToolDef(
            "draft_comment", "Draft a review comment on one line of the pull request, for the person to read, edit or "
            "drop before they decide whether to submit a review. path: the file, from the repository's root. line: its "
            "number in the pull request's version of the file, a line the diff shows. side: RIGHT (the default) or "
            "LEFT for a removed line, by its old number. body: the comment, in markdown: one to three plain sentences, "
            f"under 50 words, saying what's wrong and what to change (over {COMMENT_WORDS} words, not counting code "
            "blocks, is refused). Nothing is posted.",
            _object({"path": STRING, "line": {"type": "integer"}, "side": {"type": "string", "enum": ["RIGHT", "LEFT"]},
                     "body": STRING}, ["path", "line", "body"])))
    if reads_github:
        tools += [
            ToolDef("read_file", "Read a file of the repository as it is at the pull request's head, with line numbers. "
                    "path: from the repository's root. start, end: a range of lines, up to 400 at a time.",
                    _object({"path": STRING, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"])),
            ToolDef("list_files", "List a folder of the repository at the pull request's head. path: from the root; "
                    "empty for the root.", _object({"path": STRING}, [])),
        ]
    return tools


class ReviewTools:
    """The tools of one review's reviewer, called in the daemon's own process."""

    def __init__(self, reviews: Reviews, pr: str, drafts=None, task_id: str | None = None, author: str = "reviewer",
                 reads_github: bool = False, drafting: bool = True):
        repo, number = pr.rsplit("#", 1)
        self.reviews, self.repo, self.number = reviews, repo, int(number)
        self.drafts, self.task_id, self.author = drafts, task_id, author
        self.reads_github, self.drafting = reads_github, drafting

    def definitions(self):
        return _review_tools(self.reads_github, self.drafting)

    async def call(self, name: str, args: dict[str, Any]) -> str:
        import asyncio

        from crewbots.crew_tools import ToolRefused

        try:
            match name:
                case "pr_overview":
                    return await asyncio.to_thread(self.reviews.overview, self.repo, self.number)
                case "pr_diff":
                    return await asyncio.to_thread(self.reviews.diff_part, self.repo, self.number,
                                                   str(args["path"]) if args.get("path") else None,
                                                   int(args.get("part") or 1))
                case "draft_comment" if self.drafting and self.drafts is not None:
                    if (words := len(re.sub(r"```.*?(```|$)", "", str(args["body"]), flags=re.S).split())) > COMMENT_WORDS:
                        raise ReviewError(f"that comment is {words} words: make it one to three plain sentences, under "
                                          "50, saying what's wrong and what to change. The why belongs in your reply.")
                    draft = await asyncio.to_thread(self.reviews.check_draft, self.repo, self.number, str(args["path"]),
                                                    int(args["line"]), str(args.get("side") or "RIGHT").upper())
                    saved = self.drafts.add_draft(self.repo, self.number, draft["path"], draft["line"], draft["side"],
                                                  str(args["body"]), self.author, self.task_id, draft["commit_sha"])
                    return (f"Drafted comment {saved['id']} on {saved['path']}:{saved['line']}. It stays private until "
                            "the person submits a review.")
                case "read_file" if self.reads_github:
                    return await asyncio.to_thread(self.reviews.read_file, self.repo, self.number, str(args["path"]),
                                                   int(args.get("start") or 1), int(args["end"]) if args.get("end") else None)
                case "list_files" if self.reads_github:
                    return await asyncio.to_thread(self.reviews.list_files, self.repo, self.number, str(args.get("path") or ""))
        except ReviewError as e:
            raise ToolRefused(str(e)) from None
        except (GitHubError, KeyError, ValueError) as e:
            raise ToolRefused(f"{name} failed: {e}") from None
        raise ToolRefused(f"{name} is not one of the reviewer's tools")
