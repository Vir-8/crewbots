"""Code review: the pull requests waiting on you, and private reviews of them, read with gh and posted nowhere."""

import json
import subprocess
import time
from pathlib import Path

import pytest
from fakes import FakeBackend, bot, git_repo, make_crew
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.reviews import ReviewError, Reviews
from crewbots.runner import Runner
from crewbots.store import Store

NODE = {"number": 7, "title": "Add CSV export", "url": "https://github.com/vir/app/pull/7", "isDraft": False,
        "createdAt": "2026-10-25T10:00:00Z", "updatedAt": "2026-10-26T10:00:00Z", "additions": 12, "deletions": 3,
        "changedFiles": 2, "reviewDecision": "REVIEW_REQUIRED", "headRefName": "csv", "baseRefName": "main",
        "author": {"login": "sam", "avatarUrl": None}, "repository": {"nameWithOwner": "vir/app"},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]}}
DETAIL = {"number": 7, "title": "Add CSV export", "body": "Adds an export. Fixes part of #5.",
          "url": "https://github.com/vir/app/pull/7",
          "state": "OPEN", "isDraft": False, "author": {"login": "sam"}, "headRefName": "csv", "baseRefName": "main",
          "headRefOid": "", "additions": 12, "deletions": 3, "changedFiles": 2, "reviewDecision": None,
          "createdAt": "2026-10-25T10:00:00Z", "updatedAt": "2026-10-26T10:00:00Z", "labels": [],
          "files": [{"path": "export.py", "additions": 12, "deletions": 3}],
          "statusCheckRollup": [{"name": "lint", "conclusion": "SUCCESS"}, {"name": "tests", "conclusion": "FAILURE"},
                                {"context": "deploy", "state": "PENDING"}],
          "reviews": [{"author": {"login": "lee"}, "state": "COMMENTED", "body": "Does this handle commas?"}],
          "comments": [], "closingIssuesReferences": []}
DIFF = "diff --git a/export.py b/export.py\n+def export(): ...\n"


class FakeGh:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.detail = dict(DETAIL)

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["api", "graphql"]:
            return json.dumps({"data": {"direct": {"nodes": [NODE, {}]}, "assigned": {"nodes": [NODE]}, "team": {"nodes": []},
                                        "mine": {"nodes": []}}})
        if args[:2] == ["pr", "view"]:
            return json.dumps(self.detail)
        if args[:2] == ["pr", "diff"]:
            return DIFF
        if args[:3] == ["issue", "view", "5"]:
            return json.dumps({"number": 5, "title": "Reports need an export", "body": "Users want CSV."})
        raise AssertionError(f"gh {args} would write to GitHub")


def test_the_inbox_and_a_pull_request_come_from_gh_and_are_cached(tmp_path):
    gh = FakeGh()
    reviews = Reviews(tmp_path, gh=gh)

    inbox = reviews.inbox()
    assert [p["number"] for p in inbox["direct"]] == [7] and inbox["direct"][0]["checks"] == "SUCCESS"
    assert [p["number"] for p in inbox["assigned"]] == [7]
    reviews.inbox()
    assert len(gh.calls) == 1  # cached
    pull = reviews.pull("vir/app", 7)
    assert pull["checks"] == {"total": 3, "passed": 1, "failed": 1} and pull["checkout"] is None
    assert pull["failing"] == ["tests"] and pull["issues"] == [5]
    assert reviews.diff("vir/app", 7, limit=20).endswith("more characters)")
    with pytest.raises(ReviewError, match="owner/name"):
        reviews.pull("not a repo", 1)


def test_a_review_reads_the_prs_own_code_when_the_repo_has_a_local_copy(tmp_path):
    origin = git_repo(tmp_path / "origin")
    run = lambda *a, cwd=origin: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True)  # noqa: E731
    run("checkout", "-q", "-b", "csv")
    (origin / "export.py").write_text("def export(): ...\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "csv")
    head = run("rev-parse", "HEAD").stdout.strip()
    run("update-ref", "refs/pull/7/head", head)  # how GitHub publishes a PR's head
    run("checkout", "-q", "main")
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True)
    gh = FakeGh()
    gh.detail["headRefOid"] = head
    reviews = Reviews(tmp_path, gh=gh, worktrees=tmp_path / "worktrees")
    reviews.set_repo("vir/app", str(local))

    title, goal = reviews.brief("vir/app", 7, note="Mind the filters.")
    folder = reviews.folder("vir/app", 7)

    assert title == "Review vir/app#7: Add CSV export"
    assert "look at in particular: Mind the filters." in goal and "checked out at its head" in goal
    assert "+def export(): ..." in goal and "### #5: Reports need an export\n\nUsers want CSV." in goal
    assert "## Failing checks\n\n- tests" in goal and "**lee** (review (commented)): Does this handle commas?" in goal
    assert folder and (Path(folder) / "export.py").exists()
    assert reviews.worktree("vir/app", 7, head) == Path(folder)  # reused, not checked out again
    with pytest.raises(ReviewError, match="isn't a git checkout"):
        reviews.set_repo("vir/other", str(tmp_path))


def test_each_reviewer_model_reviews_side_by_side_apart_from_the_crew(tmp_path):
    store = Store(tmp_path / "crew.db")
    claude, codex = FakeBackend(), FakeBackend()
    runner = Runner(store, make_crew(tmp_path, bot("reviewer")), {"fake": FakeBackend(), "claude": claude, "codex": codex})
    auth = {"Authorization": "Bearer t"}
    app = create_app(runner, "t", 8765, reviews=Reviews(tmp_path, gh=FakeGh()))
    with TestClient(app, base_url="http://127.0.0.1:8765/v1") as client:
        assert client.get("/reviews/reviewers", headers=auth).json() == [
            {"backend": "claude", "model": "sonnet", "effort": "high"}, {"backend": "codex", "model": None, "effort": "high"}]
        assert client.get("/reviews/inbox", headers=auth).json()["direct"][0]["reviewed_at"] is None
        tasks = client.post("/reviews/vir/app/7/review", json={}, headers=auth).json()
        assert [t["bot"] for t in tasks] == ["reviewer:claude:sonnet:high", "reviewer:codex:default:high"]
        assert all(t["pr"] == "vir/app#7" and t["kind"] == "review" for t in tasks)
        for _ in range(200):
            if len(claude.specs) == 1 and len(codex.specs) == 1:
                break
            time.sleep(0.01)
        spec = claude.specs[0]
        assert spec.model == "sonnet" and spec.effort == "high" and spec.access.value == "read_only"
        assert spec.system_prompt.startswith("You are a code reviewer.") and not spec.mcp_servers  # none of the crew's
        assert codex.specs[0].model is None and codex.specs[0].effort == "high"
        pull = client.get("/reviews/vir/app/7", headers=auth).json()
        assert sorted(r["id"] for r in pull["reviews"]) == sorted(t["id"] for t in tasks)
        assert client.get("/reviews/inbox", headers=auth).json()["direct"][0]["reviewed_at"] is not None
        assert client.get("/reviews/vir/app/7/diff", headers=auth).json()["diff"] == DIFF
        saved = client.put("/reviews/reviewers", json=[{"backend": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"}],
                           headers=auth)
        assert saved.json() == [{"backend": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"}]
        assert client.put("/reviews/reviewers", json=[], headers=auth).status_code == 422
        assert store.threads_of("reviewer") == [] and store.activity("2000") == []  # not the crew's work
    store.close()
