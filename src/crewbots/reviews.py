"""Code review, apart from the always-on crew: the pull requests waiting on you, and private reviews of them.

The reviewer isn't one of your bots. It's its own agent, with its own instructions (`REVIEWER`), run on each of the
models you choose (`reviewers:` in `reviews.yaml`), side by side: two model families catch different things. Each
review is a conversation you can carry on.

What a reviewer gets: the pull request's title, description, author and branches; the issues it refers to; its files
and diff; which checks fail; and what's been said on it so far. When the repo is mapped to your local copy (`repos:`
in `reviews.yaml`), also the pull request's own code, checked out at its head in a worktree of crewbots'.

Everything is read with your own `gh` login, and nothing goes back to GitHub.
"""

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from crewbots.github import GitHubError, _gh

REPOS_FILE = "reviews.yaml"  # in the crew's folder: {repos: {owner/name: local checkout}, reviewers: [...]}
WORKTREES = Path.home() / ".crewbots" / "worktrees" / "reviews"
BLANK = Path.home() / ".crewbots" / "reviews" / "no-checkout"  # where a reviewer works when there's no local copy
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

QUERY = """
query {
  direct: search(query: "is:pr is:open archived:false user-review-requested:@me", type: ISSUE, first: 50) { nodes { ...pr } }
  assigned: search(query: "is:pr is:open archived:false assignee:@me", type: ISSUE, first: 50) { nodes { ...pr } }
  team: search(query: "is:pr is:open archived:false review-requested:@me -user-review-requested:@me", type: ISSUE,
               first: 50) { nodes { ...pr } }
  mine: search(query: "is:pr is:open archived:false author:@me", type: ISSUE, first: 50) { nodes { ...pr } }
}
fragment pr on PullRequest {
  number title url isDraft createdAt updatedAt additions deletions changedFiles reviewDecision headRefName baseRefName
  author { login avatarUrl } repository { nameWithOwner }
  commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
}
"""
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
    def __init__(self, crew_home: Path, gh=_gh, worktrees: Path = WORKTREES):
        self.home = crew_home
        self.gh = gh
        self.worktrees = worktrees
        self._cache: dict[str, _Cached] = {}

    # What's waiting.

    def inbox(self, fresh: bool = False) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            data = (json.loads(self.gh(["api", "graphql", "-f", f"query={QUERY}"])).get("data") or {})
            return {key: [_summary(n) for n in (data.get(key) or {}).get("nodes") or [] if n and "number" in n]
                    for key in ("direct", "assigned", "team", "mine")}
        return self._cached("inbox", INBOX_TTL_S, load, fresh)

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
        return self._cached(f"pull:{repo}#{number}", DETAIL_TTL_S, load)

    def diff(self, repo: str, number: int, limit: int = VIEW_DIFF_CHARS) -> str:
        _check(repo)
        text = self._cached(f"diff:{repo}#{number}", DETAIL_TTL_S,
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
                 "You have the diff, not the repository: say where a finding would need the surrounding code to confirm.")
        sections.insert(1, where)
        return f"Review {repo}#{number}: {pr['title']}", "\n\n".join(sections)

    def folder(self, repo: str, number: int) -> str:
        """Where a reviewer works: the pull request's head in a worktree of your copy, or an empty folder."""
        if self.checkout_of(repo):
            return str(self.worktree(repo, number, self.pull(repo, number)["head_sha"]))
        BLANK.mkdir(parents=True, exist_ok=True)
        return str(BLANK)

    def issue(self, repo: str, number: int) -> dict[str, Any] | None:
        def load() -> dict[str, Any] | None:
            try:
                raw = json.loads(self.gh(["issue", "view", str(number), "--repo", repo, "--json", "number,title,body"]))
            except (GitHubError, ValueError):
                return None  # a pull request, or gone
            return {"number": raw["number"], "title": raw.get("title", ""), "body": raw.get("body") or ""}
        return self._cached(f"issue:{repo}#{number}", DETAIL_TTL_S * 5, load)

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

    def _cached(self, key: str, ttl: float, load, fresh: bool = False):
        hit = self._cache.get(key)
        if hit and not fresh and time.monotonic() - hit.at < ttl:
            return hit.value
        value = load()
        self._cache[key] = _Cached(time.monotonic(), value)
        return value


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
