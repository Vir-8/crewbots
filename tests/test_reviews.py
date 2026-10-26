"""Code review: the pull requests waiting on you, and private reviews of them, read with gh and posted nowhere."""

import json
import subprocess
import time
from pathlib import Path

import pytest
from fakes import FakeBackend, bot, git_repo, make_crew
from fastapi.testclient import TestClient

from crewbots import github
from crewbots.api import create_app
from crewbots.github import GitHubError
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


FILES = {"export.py": "def export(): ...\n" + "".join(f"line {n}\n" for n in range(2, 600)), "logo.png": None}


class FakeGh:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.detail = dict(DETAIL)

    def objects(self, query):
        import re

        if "Tree" in query:  # a folder listing
            return {"object": {"entries": [{"name": "export.py", "type": "blob"}, {"name": "web", "type": "tree"}]}}
        out = {}
        for alias, expression in re.findall(r'(\w+): object\(expression: "([^"]*)"\)', query):
            path = expression.split(":", 1)[1]
            if path in FILES:
                out[alias] = {"text": FILES[path], "isBinary": FILES[path] is None}
            else:
                out[alias] = None
        return out

    def __call__(self, args):
        self.calls.append(args)
        if args[:2] == ["api", "graphql"] and "object(expression" in args[-1]:
            return json.dumps({"data": {"repository": self.objects(args[-1])}})
        if args[:3] == ["api", "-X", "GET"]:
            return json.dumps({"items": [{"path": "export.py", "text_matches": [{"fragment": "def export(): ..."}]}]})
        if args[:2] == ["api", "graphql"]:
            searches = [line for line in args[-1].splitlines() if "search(" in line]
            assert all("-author:@me" in s for s in searches if "mine:" not in s)  # your own only in their group
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

    inbox, error = reviews.inbox()
    assert [p["number"] for p in inbox["direct"]] == [7] and inbox["direct"][0]["checks"] == "SUCCESS"
    assert [p["number"] for p in inbox["assigned"]] == [7] and "team" not in inbox and error is None
    reviews.inbox()
    assert len(gh.calls) == 3  # one a list, then kept
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


def test_without_a_local_copy_the_reviewer_reads_github_scoped_to_what_it_needs(tmp_path):
    gh = FakeGh()
    gh.detail = dict(DETAIL, headRefOid="abc123", files=[{"path": "export.py", "additions": 1, "deletions": 0},
                                                       {"path": "logo.png", "additions": 0, "deletions": 0}])
    reviews = Reviews(tmp_path, gh=gh, snapshots=tmp_path / "snapshots")

    folder = Path(reviews.folder("vir/app", 7))

    assert sorted(p.name for p in folder.rglob("*") if p.is_file()) == [".complete", "export.py"]  # no binary
    assert reviews.folder("vir/app", 7) == str(folder) and len([c for c in gh.calls if "Blob" in c[-1]]) == 1  # kept
    _, goal = reviews.brief("vir/app", 7)
    assert "use your tools: read_file and list_files" in goal
    page = reviews.read_file("vir/app", 7, "export.py", 590)
    assert page.startswith("export.py at abc123, lines 590-599 of 599") and "  599  line 599" in page
    assert "lines 1-400 of 599" in reviews.read_file("vir/app", 7, "export.py") and "ask for start=401" in \
        reviews.read_file("vir/app", 7, "export.py")
    assert "isn't a text file" in reviews.read_file("vir/app", 7, "missing.py")
    assert reviews.list_files("vir/app", 7) == "export.py\nweb/"
    assert reviews.search_code("vir/app", "export") == "- export.py\n    def export(): ..."


async def test_a_reviewer_gets_its_tools_on_either_backend_but_not_with_a_local_copy(tmp_path):
    from crewbots.reviews import ReviewTools

    store = Store(tmp_path / "crew.db")
    claude, codex = FakeBackend(), FakeBackend()
    claude.in_process_tools = True
    runner = Runner(store, make_crew(tmp_path, bot("x")), {"claude": claude, "codex": codex},
                    api_url="http://127.0.0.1:8765/v1")
    reviews = Reviews(tmp_path, gh=FakeGh())
    runner.review_tools = lambda task: ReviewTools(reviews, task.pr)
    for name in ("reviewer:claude:sonnet:high", "reviewer:codex:default:high"):
        runner.start_review(name, "Review it", title="Review vir/app#7", pr="vir/app#7", folder=str(tmp_path))
    for _ in range(200):
        if claude.specs and codex.specs:
            break
        await __import__("asyncio").sleep(0.01)

    assert isinstance(claude.specs[0].crew, ReviewTools) and not claude.specs[0].mcp_servers
    server = codex.specs[0].mcp_servers["crewbots"]
    assert server.env["CREWBOTS_TOOLS"] == "review" and codex.specs[0].crew is None
    assert "def export" in await claude.specs[0].crew.call("search_code", {"query": "export"})
    runner.review_tools = lambda task: None  # a mapped local copy: the worktree has everything
    runner.start_review("reviewer:codex:default:high", "Again", title="t", pr="vir/app#7", folder=str(tmp_path))
    for _ in range(200):
        if len(codex.specs) == 2:
            break
        await __import__("asyncio").sleep(0.01)
    assert codex.specs[1].mcp_servers == {}
    await runner.shutdown()
    store.close()



# When GitHub fails


def gh_answering(monkeypatch, *outputs):
    """gh that answers with `outputs` in turn, each (exit code, stderr), with "ok" on stdout when it succeeds."""
    monkeypatch.setattr(github, "GH_WAITS_S", (0.0, 0.0))
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        code, err = outputs[len(calls) - 1]
        return subprocess.CompletedProcess(argv, code, "ok" if code == 0 else "", err)

    monkeypatch.setattr(github.subprocess, "run", run)
    return calls


def test_gh_tries_again_when_github_fails_for_a_passing_reason(monkeypatch):
    calls = gh_answering(monkeypatch, (1, "gh: HTTP 504"), (1, "Post https://api.github.com/graphql: EOF"), (0, ""))

    assert github._gh(["api", "graphql"]) == "ok" and len(calls) == 3


def test_gh_gives_up_after_its_tries_and_never_retries_what_would_fail_again(monkeypatch):
    calls = gh_answering(monkeypatch, *[(1, "gh: HTTP 502: Server Error")] * 3)
    with pytest.raises(GitHubError, match="502") as failed:
        github._gh(["api", "graphql"])
    assert len(calls) == 3 and failed.value.passing

    calls = gh_answering(monkeypatch, (1, "GraphQL: Could not resolve to a Repository with the name 'vir/nope'."))
    with pytest.raises(GitHubError) as failed:
        github._gh(["api", "graphql"])
    assert len(calls) == 1 and not failed.value.passing


def test_the_inbox_asks_each_list_on_its_own(tmp_path):
    asked = []

    def gh(args):
        searches = [line.split(":")[0].strip() for line in args[-1].splitlines() if "search(" in line]
        asked.append(searches)
        return json.dumps({"data": {searches[0]: {"nodes": [NODE] if searches[0] == "direct" else []}}})

    reviews = Reviews(tmp_path, gh=gh)
    inbox, _ = reviews.inbox()

    assert sorted(asked) == [["assigned"], ["direct"], ["mine"]]  # your teams' only when you open it
    assert [p["number"] for p in inbox["direct"]] == [7] and inbox["assigned"] == []
    assert reviews.inbox(("team",))[0] == {"team": []} and sorted(asked)[-1] == ["team"]


def test_a_kept_list_comes_back_at_once_and_is_refreshed_behind_it(tmp_path, monkeypatch):
    from crewbots import reviews as reviews_module

    answers = iter([[NODE], [NODE, {**NODE, "number": 8}]])
    fetched = []

    def gh(args):
        fetched.append(1)
        return json.dumps({"data": {"direct": {"nodes": next(answers)}}})

    reviews = Reviews(tmp_path, gh=gh)
    assert [p["number"] for p in reviews.inbox(("direct",))[0]["direct"]] == [7]
    monkeypatch.setattr(reviews_module, "INBOX_TTL_S", 0.0)  # it's old now

    again, error = reviews.inbox(("direct",))
    assert [p["number"] for p in again["direct"]] == [7] and error is None  # the kept one, at once
    for _ in range(100):
        if len(fetched) == 2 and "inbox:direct" not in reviews._loading:
            break
        time.sleep(0.01)
    monkeypatch.setattr(reviews_module, "INBOX_TTL_S", 60.0)
    assert [p["number"] for p in reviews.inbox(("direct",))[0]["direct"]] == [7, 8]  # the new one, fetched behind it


def test_when_github_fails_the_last_good_list_stays_up(tmp_path):
    fail = False

    def gh(args):
        if fail:
            raise GitHubError("gh: HTTP 504", passing=True)
        return json.dumps({"data": {"direct": {"nodes": [NODE]}}})

    reviews = Reviews(tmp_path, gh=gh)
    reviews.inbox(("direct",))
    fail = True

    inbox, error = reviews.inbox(("direct",), fresh=True)
    assert [p["number"] for p in inbox["direct"]] == [7] and error == "gh: HTTP 504"
    assert Reviews(tmp_path, gh=gh).inbox(("direct",)) == ({"direct": []}, "gh: HTTP 504")  # nothing kept yet
