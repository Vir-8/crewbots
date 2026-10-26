"""Code review, apart from the always-on crew: the pull requests waiting on you, and private reviews of them.

The reviewer isn't one of your bots. It's its own agent, with its own instructions (`REVIEWER`), run on each of the
models you choose (`reviewers:` in `reviews.yaml`), side by side: two model families catch different things. Each
review is a conversation you can carry on.

What a reviewer gets: the pull request's title, description, author and branches; the issues it refers to; its files
and diff; which checks fail; and what's been said on it so far. Then the code around the change:

- With your local copy mapped (`repos:` in `reviews.yaml`), the pull request's own code, checked out at its head in a
  worktree of crewbots'.
- Without one, it's read from GitHub, scoped: the changed files at the head are fetched into the reviewer's folder,
  and it has tools to read any other file at that commit, list a folder, and search the repo's code, fetched only
  when it asks. Nothing is cloned.

Everything is read with your own `gh` login, and nothing goes back to GitHub.
"""

import json
import re
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
SNAPSHOT_FILES = 200
FILE_BYTES = 400_000  # a changed file bigger than this is left out of the snapshot; the reviewer can still read it
FETCH_BATCH = 40  # files per GraphQL query
READ_LINES = 400
SEARCH_RESULTS = 20
DEFAULT_REVIEWERS = [{"backend": "claude", "model": "sonnet", "effort": "high"},
                     {"backend": "codex", "model": None, "effort": "high"}]  # None: Codex's own default model
MAX_REVIEWERS = 4
ISSUE_CHARS = 2_500
DISCUSSION_CHARS = 5_000
REVIEWER_PREFIX = "reviewer:"  # a reviewer's name: reviewer:<backend>:<model>:<effort>
REVIEWER = """You are a code reviewer. You review one pull request, privately, for the person you're talking to.

Find what's actually wrong: bugs, regressions, broken edge cases, security problems, missing or misleading tests, and
changes that don't do what the pull request says. Check claims against the code; don't speculate where you can look.
When you have the repository, read around the change: the callers, the types, the tests, how the touched code is used
elsewhere. Run read-only commands if you can (git log, git grep, a type check) to confirm a finding.

Never change files, and never post, comment, approve, request changes or submit anything anywhere: what you find
stays in this conversation.

Reply in this shape:
1. One line: no blocking issues, or how many issues you found.
2. The findings, most serious first, each as P0 (must fix), P1 (should fix) or P2 (worth fixing): file:line, the
   problem, and the input or state that goes wrong. No style nits a linter would catch. No restating the diff.
3. Overall impact: what this change affects, and how risky it is.
4. What you couldn't check, and why.

Afterwards, answer follow-up questions about the pull request plainly and briefly."""
INBOX_TTL_S = 60.0
DETAIL_TTL_S = 120.0
PROMPT_DIFF_CHARS = 60_000
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

DETAIL_FIELDS = ("number,title,body,url,state,isDraft,author,headRefName,baseRefName,headRefOid,additions,deletions,"
                 "changedFiles,files,reviewDecision,createdAt,updatedAt,statusCheckRollup,labels,reviews,comments,"
                 "closingIssuesReferences")
ISSUE_REF = re.compile(r"(?<![\w/])#(\d{1,7})\b")


class ReviewError(ValueError):
    pass


@dataclass
class _Cached:
    at: float
    value: Any


class Reviews:
    def __init__(self, crew_home: Path, gh=_gh, worktrees: Path = WORKTREES, snapshots: Path = SNAPSHOTS):
        self.home = crew_home
        self.gh = gh
        self.worktrees = worktrees
        self.snapshots = snapshots
        self._cache: dict[str, _Cached] = {}
        self._loading: dict[str, threading.Event] = {}  # keys being fetched now, set when each is done
        self._lock = threading.Lock()

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

    # Reviewing.

    def brief(self, repo: str, number: int, note: str = "") -> tuple[str, str]:
        """The review's title, and everything a reviewer is given about the pull request."""
        pr = self.pull(repo, number)
        body = _clip(pr["body"], BODY_CHARS)
        files = "\n".join(f"- {f['path']} (+{f['additions']} −{f['deletions']})" for f in pr["files"][:200])
        facts = (f"{repo}#{number} by {pr['author']}, {pr['head']} → {pr['base']}, {pr['changed_files']} files "
                 f"(+{pr['additions']} −{pr['deletions']}), checks: {pr['checks']['passed']} of {pr['checks']['total']} "
                 f"passed, {pr['checks']['failed']} failed.")
        sections = [f"Review {pr['url']}.", f"# {pr['title']}\n\n{facts}\n\n{body}"]
        if note.strip():
            sections.insert(1, f"What I'd like you to look at in particular: {note.strip()}")
        issues = [self.issue(repo, n) for n in pr["issues"]]
        if issues := [i for i in issues if i]:
            sections.append("## The issues it refers to\n\n" + "\n\n".join(
                f"### #{i['number']}: {i['title']}\n\n{_clip(i['body'], ISSUE_CHARS)}" for i in issues))
        if pr["failing"]:
            sections.append("## Failing checks\n\n" + "\n".join(f"- {name}" for name in pr["failing"][:30]))
        if pr["discussion"]:
            said = "\n\n".join(f"**{d['author']}** ({d['kind']}): {_clip(d['body'], 800)}" for d in pr["discussion"])
            sections.append(f"## What's been said on it so far\n\n{_clip(said, DISCUSSION_CHARS)}")
        sections.append(f"## Files\n\n{files}")
        sections.append(f"## Diff\n\n```diff\n{self.diff(repo, number, PROMPT_DIFF_CHARS)}\n```")
        where = (f"Your working directory is the pull request's own code, checked out at its head ({pr['head_sha'][:12]})."
                 if self.checkout_of(repo) else
                 f"Your working directory has the changed files as they are at the pull request's head ({pr['head_sha'][:12]}), "
                 "not the whole repository. For anything else, use your tools: read_file and list_files read the "
                 "repository at that commit, and search_code searches its code (on the default branch). Fetch what a "
                 "finding depends on, such as callers, types and tests, rather than guessing.")
        sections.insert(1, where)
        return f"Review {repo}#{number}: {pr['title']}", "\n\n".join(sections)

    def folder(self, repo: str, number: int) -> str:
        """Where a reviewer works: the pull request's head in a worktree of your copy, or its changed files, fetched."""
        pr = self.pull(repo, number)
        if self.checkout_of(repo):
            return str(self.worktree(repo, number, pr["head_sha"]))
        return str(self.snapshot(repo, number, pr["head_sha"], [f["path"] for f in pr["files"]]))

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

    def search_code(self, repo: str, query: str) -> str:
        """GitHub's code search, in this repo. It searches the default branch, not the pull request."""
        raw = self.gh(["api", "-X", "GET", "search/code", "-H", "Accept: application/vnd.github.text-match+json",
                       "-f", f"q={query} repo:{repo}", "-f", f"per_page={SEARCH_RESULTS}"])
        items = json.loads(raw).get("items") or []
        if not items:
            return "Nothing in the repo matches that (code search covers the default branch)."
        out = []
        for item in items:
            fragments = [m.get("fragment", "").strip() for m in item.get("text_matches") or []][:2]
            out.append(f"- {item['path']}" + "".join(f"\n    {_clip(f, 300)}" for f in fragments))
        return "\n".join(out)

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
               worktree=False, effort=None if effort == "default" else effort)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…"


def _check(repo: str) -> None:
    if not REPO.fullmatch(repo):
        raise ReviewError(f"{repo!r} isn't owner/name")


__all__ = ["Reviews", "ReviewError", "GitHubError"]


# The reviewer's tools, when it reads from GitHub: defined once, served in-process to Claude and over MCP to Codex.

def _review_tools():
    from crewbots.crew_tools import STRING, ToolDef, _object

    return [
        ToolDef("read_file", "Read a file of the repository as it is at the pull request's head, with line numbers. "
                "path: from the repository's root. start, end: a range of lines, up to 400 at a time.",
                _object({"path": STRING, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"])),
        ToolDef("list_files", "List a folder of the repository at the pull request's head. path: from the root; "
                "empty for the root.", _object({"path": STRING}, [])),
        ToolDef("search_code", "Search the repository's code on GitHub, for callers, definitions and tests. It "
                "searches the default branch, not the pull request. query: words or an identifier.",
                _object({"query": STRING}, ["query"])),
    ]


class ReviewTools:
    """The reviewer's tools for one review, called in the daemon's own process."""

    def __init__(self, reviews: Reviews, pr: str):
        repo, number = pr.rsplit("#", 1)
        self.reviews, self.repo, self.number = reviews, repo, int(number)

    def definitions(self):
        return _review_tools()

    async def call(self, name: str, args: dict[str, Any]) -> str:
        import asyncio

        from crewbots.crew_tools import ToolRefused

        try:
            match name:
                case "read_file":
                    return await asyncio.to_thread(self.reviews.read_file, self.repo, self.number, str(args["path"]),
                                                   int(args.get("start") or 1), int(args["end"]) if args.get("end") else None)
                case "list_files":
                    return await asyncio.to_thread(self.reviews.list_files, self.repo, self.number, str(args.get("path") or ""))
                case "search_code":
                    return await asyncio.to_thread(self.reviews.search_code, self.repo, str(args["query"]))
        except (GitHubError, KeyError, ValueError) as e:
            raise ToolRefused(f"{name} failed: {e}") from None
        raise ToolRefused(f"{name} is not one of the reviewer's tools")
