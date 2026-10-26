"""Code review, apart from the always-on crew: the pull requests waiting on you, and private reviews of them by a bot.

Everything is read with your own `gh` login, and nothing goes back to GitHub: a review is a conversation between you
and the bot, kept in crewbots. When a repo is mapped to a local checkout (`reviews.yaml` in the crew's folder), the
reviewer reads the pull request's own code, checked out at its head in a worktree of crewbots', not just the diff.
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

REPOS_FILE = "reviews.yaml"  # in the crew's folder: {repos: {owner/name: path to a local checkout}}
WORKTREES = Path.home() / ".crewbots" / "worktrees" / "reviews"
INBOX_TTL_S = 60.0
DETAIL_TTL_S = 120.0
PROMPT_DIFF_CHARS = 60_000
VIEW_DIFF_CHARS = 800_000
BODY_CHARS = 6_000
REPO = re.compile(r"[\w.-]+/[\w.-]+")

QUERY = """
query {
  direct: search(query: "is:pr is:open archived:false user-review-requested:@me", type: ISSUE, first: 50) { nodes { ...pr } }
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
                 "changedFiles,files,reviewDecision,createdAt,updatedAt,statusCheckRollup,labels")


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
                    for key in ("direct", "team", "mine")}
        return self._cached("inbox", INBOX_TTL_S, load, fresh)

    def pull(self, repo: str, number: int) -> dict[str, Any]:
        _check(repo)

        def load() -> dict[str, Any]:
            raw = json.loads(self.gh(["pr", "view", str(number), "--repo", repo, "--json", DETAIL_FIELDS]))
            checks = [c for c in raw.get("statusCheckRollup") or [] if isinstance(c, dict)]
            states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in checks]
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
                           "failed": sum(s in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED")
                                         for s in states)},
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

    def brief(self, repo: str, number: int, note: str = "") -> tuple[str, str, str | None]:
        """The review's title, the goal for the reviewing bot, and the folder it works in (None: the diff alone)."""
        pr = self.pull(repo, number)
        folder = self.worktree(repo, number, pr["head_sha"]) if self.checkout_of(repo) else None
        body = pr["body"] if len(pr["body"]) <= BODY_CHARS else pr["body"][:BODY_CHARS] + "\n…"
        files = "\n".join(f"- {f['path']} (+{f['additions']} −{f['deletions']})" for f in pr["files"][:200])
        where = (f"Your working directory is the pull request's own code, checked out at its head ({pr['head_sha'][:12]}): "
                 "read around the change, and check how what it touches is used." if folder else
                 "You have the diff only; say where a finding would need the surrounding code to confirm.")
        facts = (f"{repo}#{number} by {pr['author']}, {pr['head']} → {pr['base']}, {pr['changed_files']} files "
                 f"(+{pr['additions']} −{pr['deletions']}), checks: {pr['checks']['passed']} of {pr['checks']['total']} "
                 f"passed, {pr['checks']['failed']} failed.")
        goal = f"""Review {pr['url']} privately, for me.

Look for actionable bugs, and assess the change's overall impact. Don't change any code, and don't post, comment,
approve or submit anything anywhere: this review stays between us. {where}

Start with one line: no blocking issues, or the issues found. Then the findings, most serious first (P0 to P2), each
with file:line, the problem, and the input or state that goes wrong. Leave out style nits. Then the overall impact,
and what you couldn't check.
{f"{chr(10)}My note: {note}{chr(10)}" if note.strip() else ""}
# {pr['title']}

{facts}

{body}

## Files

{files}

## Diff

```diff
{self.diff(repo, number, PROMPT_DIFF_CHARS)}
```"""
        return f"Review {repo}#{number}: {pr['title']}", goal, str(folder) if folder else None

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
        path = self.home / REPOS_FILE
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text()) or {}
        return {str(k): str(v) for k, v in (data.get("repos") or {}).items()}

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
        (self.home / REPOS_FILE).write_text(yaml.safe_dump({"repos": repos}, sort_keys=True))
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


def _check(repo: str) -> None:
    if not REPO.fullmatch(repo):
        raise ReviewError(f"{repo!r} isn't owner/name")


__all__ = ["Reviews", "ReviewError", "GitHubError"]
