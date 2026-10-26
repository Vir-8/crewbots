"""What the GitHub triggers watch, read with the user's own `gh` login: pull requests that ask the user (not one of their
teams) for a review, and the user's own pull requests whose checks failed. Nothing listens for webhooks; the daemon
asks, every few minutes.
"""

import json
import re
import subprocess
import time
from dataclasses import dataclass, field

GH_TIMEOUT_S = 60
GH_WAITS_S = (2.0, 5.0)  # before each new try of a call that failed for a passing reason
# GitHub's own passing failures, as gh reports them: its servers and gateways (502, 503, 504, and GraphQL's
# "something went wrong", which is its query timeout), and the network on the way there.
PASSING = re.compile(r"HTTP 5\d\d|something went wrong|timeout|timed out|connection (reset|refused)|\bEOF\b|"
                     r"TLS handshake|temporarily unavailable|no such host|network is unreachable", re.IGNORECASE)
DIFF_CHARS = 60_000
LOG_LINES = 80  # of each failed job's log
QUERY = """
query {
  review: search(query: "is:pr is:open archived:false user-review-requested:@me", type: ISSUE, first: 30) { nodes { ...pr } }
  mine: search(query: "is:pr is:open archived:false author:@me", type: ISSUE, first: 30) { nodes { ...pr } }
}
fragment pr on PullRequest {
  number title url headRefName headRefOid author { login } repository { nameWithOwner }
  commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 50) { nodes {
    __typename ... on CheckRun { name conclusion detailsUrl } ... on StatusContext { context state targetUrl }
  } } } } } }
}
"""
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "CANCELLED"}
JOB_URL = re.compile(r"/actions/runs/\d+/job/(\d+)")


class GitHubError(RuntimeError):
    def __init__(self, message: str, passing: bool = False):
        super().__init__(message)
        self.passing = passing  # GitHub or the network failed this time; the same call may well work in a moment


@dataclass(frozen=True)
class PullRequest:
    repo: str
    number: int
    title: str
    url: str
    author: str
    branch: str
    head: str  # the head commit: a new push is a new version, and can trigger again
    checks: str | None = None  # the overall state of its checks: SUCCESS, FAILURE, PENDING, ...
    failed: tuple[tuple[str, str | None], ...] = field(default=())  # (check name, its details URL)

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}@{self.head}"


@dataclass(frozen=True)
class Snapshot:
    review_requested: list[PullRequest]
    mine: list[PullRequest]


def fetch() -> Snapshot:
    data = _gh(["api", "graphql", "-f", f"query={QUERY}"])
    result = json.loads(data).get("data") or {}
    return Snapshot(review_requested=_prs(result.get("review")), mine=_prs(result.get("mine")))


def diff(pr: PullRequest) -> str:
    text = _gh(["pr", "diff", str(pr.number), "--repo", pr.repo])
    if len(text) > DIFF_CHARS:
        return text[:DIFF_CHARS] + f"\n… (the diff goes on for {len(text) - DIFF_CHARS} more characters: gh pr diff)"
    return text


def failures(pr: PullRequest) -> str:
    """Each failed check, with the end of its log when it ran on GitHub Actions."""
    sections = []
    for name, url in pr.failed:
        section = f"## {name}" + (f"\n{url}" if url else "")
        job = JOB_URL.search(url or "")
        if job:
            try:
                log = _gh(["run", "view", "--job", job.group(1), "--log-failed", "--repo", pr.repo])
                tail = "\n".join(log.splitlines()[-LOG_LINES:])
                section += f"\n\n```\n{tail}\n```"
            except GitHubError as e:
                section += f"\n\n(couldn't read its log: {e})"
        sections.append(section)
    return "\n\n".join(sections) or "(no failed checks are listed)"


def _prs(search: dict | None) -> list[PullRequest]:
    prs = []
    for node in (search or {}).get("nodes") or []:
        if not node or "number" not in node:
            continue
        commit = ((node.get("commits") or {}).get("nodes") or [{}])[-1].get("commit") or {}
        rollup = commit.get("statusCheckRollup") or {}
        failed = []
        for c in ((rollup.get("contexts") or {}).get("nodes") or []):
            if c.get("__typename") == "CheckRun" and c.get("conclusion") in FAILED:
                failed.append((c.get("name", "check"), c.get("detailsUrl")))
            elif c.get("__typename") == "StatusContext" and c.get("state") in FAILED:
                failed.append((c.get("context", "status"), c.get("targetUrl")))
        prs.append(PullRequest(
            repo=node["repository"]["nameWithOwner"], number=node["number"], title=node.get("title", ""),
            url=node.get("url", ""), author=(node.get("author") or {}).get("login", ""),
            branch=node.get("headRefName", ""), head=node.get("headRefOid", ""), checks=rollup.get("state"),
            failed=tuple(failed),
        ))
    return prs


def _gh(args: list[str], stdin: str | None = None) -> str:
    """Runs gh, and again after a short wait when GitHub or the network failed for a passing reason. With `stdin` (a
    request body: a write), it runs once, since a write that timed out may have happened anyway."""
    if stdin is not None:
        return _gh_once(args, stdin)
    for attempt, wait in enumerate((0.0, *GH_WAITS_S)):
        time.sleep(wait)
        try:
            return _gh_once(args)
        except GitHubError as e:
            if not e.passing or attempt == len(GH_WAITS_S):
                raise
    raise AssertionError("unreachable")


def _gh_once(args: list[str], stdin: str | None = None) -> str:
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT_S,
                              **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}))
    except FileNotFoundError:
        raise GitHubError("the GitHub CLI (gh) isn't installed") from None
    except subprocess.TimeoutExpired:
        raise GitHubError(f"gh {args[0]} took more than {GH_TIMEOUT_S}s", passing=True) from None
    if done.returncode != 0:
        output = (done.stderr or done.stdout).strip()
        message = output.splitlines()[-1] if output else f"gh exited with {done.returncode}"
        raise GitHubError(message, passing=bool(PASSING.search(output)))
    return done.stdout
