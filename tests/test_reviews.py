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

    def __call__(self, args, stdin=None):
        self.calls.append(args)
        if args[:2] == ["api", "graphql"] and "object(expression" in args[-1]:
            return json.dumps({"data": {"repository": self.objects(args[-1])}})
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
    assert pull["failing"] == ["tests"] and pull["issues"] == [5] and pull["people"] == ["sam", "lee"]
    assert reviews.diff("vir/app", 7, limit=20).endswith("more characters)")
    with pytest.raises(ReviewError, match="owner/name"):
        reviews.pull("not a repo", 1)


def origin_with_pr(tmp_path: Path) -> tuple[Path, str]:
    """A repository standing in for GitHub's, with pull request 7's head published where GitHub publishes it."""
    origin = git_repo(tmp_path / "origin")
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True, text=True)  # noqa: E731
    run("checkout", "-q", "-b", "csv")
    (origin / "export.py").write_text("def export(): ...\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "csv")
    head = run("rev-parse", "HEAD").stdout.strip()
    run("update-ref", "refs/pull/7/head", head)
    run("checkout", "-q", "main")
    run("config", "uploadpack.allowFilter", "true")  # serves a blobless clone, as GitHub does
    return origin, head


def test_a_review_reads_the_prs_own_code_when_the_repo_has_a_local_copy(tmp_path):
    origin, head = origin_with_pr(tmp_path)
    local = tmp_path / "local"
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True)
    gh = FakeGh()
    gh.detail["headRefOid"] = head
    reviews = Reviews(tmp_path, gh=gh, worktrees=tmp_path / "worktrees", snapshots=tmp_path / "reviews",
                      remote="file:///nowhere")
    reviews.set_repo("vir/app", str(local))

    folder, whole = reviews.checkout("vir/app", 7)

    assert whole and folder.parent == tmp_path / "worktrees" and (folder / "export.py").exists()
    assert reviews.worktree("vir/app", 7, head) == folder  # reused, not checked out again
    assert not (tmp_path / "reviews" / "repos").exists()  # no clone of its own
    with pytest.raises(ReviewError, match="isn't a git checkout"):
        reviews.set_repo("vir/other", str(tmp_path))


def test_without_a_local_copy_crewbots_clones_the_repo_once_and_checks_out_each_pull_request(tmp_path, monkeypatch):
    from crewbots import reviews as reviews_module

    origin, head = origin_with_pr(tmp_path)
    gh = FakeGh()
    gh.detail["headRefOid"] = head
    reviews = Reviews(tmp_path, gh=gh, snapshots=tmp_path / "reviews", remote=f"file://{origin}")

    folder, whole = reviews.checkout("vir/app", 7)

    assert whole and folder.parent == tmp_path / "reviews" / "checkouts" and not reviews.is_snapshot(str(folder))
    assert (folder / "export.py").read_text() == "def export(): ...\n" and (folder / "README.md").exists()
    clone = tmp_path / "reviews" / "repos" / "vir__app"
    assert subprocess.run(["git", "-C", str(clone), "config", "remote.origin.partialclonefilter"],
                          capture_output=True, text=True).stdout.strip() == "blob:none"
    assert reviews.cached_checkout("vir/app", 7, head) == folder  # kept
    monkeypatch.setattr(reviews_module, "KEEP_CHECKOUTS", 1)
    main = subprocess.run(["git", "-C", str(origin), "rev-parse", "main"], capture_output=True, text=True).stdout.strip()
    time.sleep(0.01)
    other = reviews.cached_checkout("vir/app", 8, main)  # another pull request: the same clone, no fetch needed
    assert other.exists() and not folder.exists()  # the oldest checkout makes way
    assert not gh.calls or all(c[:2] != ["api", "graphql"] for c in gh.calls)  # GitHub's API isn't read for code


def test_a_pull_request_comes_to_a_reviewer_through_its_tools_a_part_at_a_time(tmp_path, monkeypatch):
    from crewbots import reviews as reviews_module

    gh = FakeGh()
    reviews = Reviews(tmp_path, gh=gh)
    overview = reviews.overview("vir/app", 7)
    assert overview.startswith("# Add CSV export\nhttps://github.com/vir/app/pull/7")
    assert "checks: 1 of 3 passed, 1 failed (tests)" in overview and "- export.py (+12 −3)" in overview
    assert "### #5: Reports need an export\n\nUsers want CSV." in overview
    assert "**lee** (review (commented)): Does this handle commas?" in overview and "+def export" not in overview

    big = "".join(f"diff --git a/f{n}.py b/f{n}.py\n" + "+x\n" * 30 for n in range(5))
    reviews._cache.pop("diff:vir/app#7", None)
    reviews.gh = lambda args, stdin=None: big if args[:2] == ["pr", "diff"] else gh(args)
    monkeypatch.setattr(reviews_module, "DIFF_PART_CHARS", 250)
    first = reviews.diff_part("vir/app", 7)
    assert first.startswith("Part 1 of 3; ask for part 2 next.") and "f0.py" in first and "f2.py" not in first
    assert reviews.diff_part("vir/app", 7, part=3).startswith("Part 3 of 3.")
    assert reviews.diff_part("vir/app", 7, path="f3.py").startswith("diff --git a/f3.py b/f3.py")
    with pytest.raises(ReviewError, match="isn't one of this pull request's changed files"):
        reviews.diff_part("vir/app", 7, path="nope.py")
    with pytest.raises(ReviewError, match="3 part"):
        reviews.diff_part("vir/app", 7, part=9)


def test_one_review_the_reviewers_findings_put_together_by_the_lead(tmp_path):
    from crewbots.reviews import LEAD_REVIEWER

    origin, head = origin_with_pr(tmp_path)
    gh = FakeGh()
    gh.detail["headRefOid"] = head
    store = Store(tmp_path / "crew.db")
    gate = __import__("asyncio").Event()
    claude, codex = FakeBackend(), FakeBackend(gate=gate)
    runner = Runner(store, make_crew(tmp_path, bot("reviewer")), {"fake": FakeBackend(), "claude": claude, "codex": codex})
    auth = {"Authorization": "Bearer t"}
    reviews = Reviews(tmp_path, gh=gh, snapshots=tmp_path / "reviews", remote=f"file://{origin}")
    app = create_app(runner, "t", 8765, reviews=reviews)

    def until(condition):
        for _ in range(500):
            if condition():
                return
            time.sleep(0.01)
        raise TimeoutError

    with TestClient(app, base_url="http://127.0.0.1:8765/v1") as client:
        assert client.get("/reviews/reviewers", headers=auth).json() == [
            {"backend": "claude", "model": "sonnet", "effort": "high"}, {"backend": "codex", "model": None, "effort": "high"}]
        assert client.get("/reviews/inbox", headers=auth).json()["direct"][0]["reviewed_at"] is None
        [lead] = client.post("/reviews/vir/app/7/review", json={"note": "Mind the filters."}, headers=auth).json()
        assert lead["bot"] == "reviewer:claude:sonnet:high" and lead["parent_id"] is None and lead["kind"] == "review"
        assert lead["title"] == "Review vir/app#7: Add CSV export" and lead["status"] == "queued"
        until(lambda: len(claude.specs) == 1 and len(codex.specs) == 1)  # each reviewer, on its own
        children = store.children(lead["id"])
        assert sorted(c.bot for c in children) == ["reviewer:claude:sonnet:high", "reviewer:codex:default:high"]

        mine = claude.specs[0]
        assert mine.prompt.startswith("Review https://github.com/vir/app/pull/7.") and len(mine.prompt) < 600
        assert "Mind the filters." in mine.prompt and "+def export" not in mine.prompt  # the diff is a tool away
        assert (Path(mine.cwd) / "export.py").exists() and mine.access.value == "read_only" and mine.network
        assert mine.system_prompt.startswith("You are one of the reviewers") and not mine.mcp_servers
        assert "Never run tests, builds" in mine.system_prompt
        client.post(f"/tasks/{lead['id']}/messages", json={"text": "And the encoding?"}, headers=auth)
        time.sleep(0.1)
        assert len(claude.specs) == 1  # the lead waits for the reviewers

        client.portal.call(gate.set)  # on the app's own loop
        until(lambda: len(claude.specs) == 3)
        findings, follow_up = claude.specs[1], claude.specs[2]
        assert findings.system_prompt == LEAD_REVIEWER and findings.cwd == mine.cwd
        assert findings.prompt.startswith("The reviewers have finished: Review vir/app#7: Add CSV export.")
        assert "## claude sonnet, high effort\n\ndid: Review" in findings.prompt
        assert "## codex (its default model), high effort" in findings.prompt
        assert follow_up.prompt == "And the encoding?"

        pull = client.get("/reviews/vir/app/7", headers=auth).json()
        assert sorted(r["id"] for r in pull["reviews"]) == sorted([lead["id"], *(c.id for c in children)])
        assert client.get("/reviews/inbox", headers=auth).json()["direct"][0]["reviewed_at"] is not None
        assert client.get("/reviews/vir/app/7/diff", headers=auth).json()["diff"] == DIFF
        saved = client.put("/reviews/reviewers", json=[{"backend": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"}],
                           headers=auth)
        assert saved.json() == [{"backend": "codex", "model": "gpt-6.1-sol", "effort": "xhigh"}]
        assert client.put("/reviews/reviewers", json=[], headers=auth).status_code == 422
        assert store.threads_of("reviewer") == [] and store.activity("2000") == []  # not the crew's work
    store.close()


def test_when_the_code_cant_be_had_the_review_fails_and_says_why(tmp_path):
    gh = FakeGh()
    gh.detail["headRefOid"] = "abc123"
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("reviewer")), {"fake": FakeBackend(), "claude": FakeBackend(),
                                                                  "codex": FakeBackend()})
    reviews = Reviews(tmp_path, gh=gh, snapshots=tmp_path / "reviews", remote="file:///nowhere")
    reviews.snapshot = lambda *a: (_ for _ in ()).throw(GitHubError("gh: HTTP 404"))
    with TestClient(create_app(runner, "t", 8765, reviews=reviews), base_url="http://127.0.0.1:8765/v1") as client:
        [lead] = client.post("/reviews/vir/app/7/review", json={}, headers={"Authorization": "Bearer t"}).json()
        for _ in range(300):
            if store.get_task(lead["id"]).status.value == "failed":
                break
            time.sleep(0.01)
        task = store.get_task(lead["id"])
        assert task.status.value == "failed" and "couldn't get the pull request's code: gh: HTTP 404" in task.error
        assert store.children(lead["id"]) == []
    store.close()


def test_without_the_repository_the_reviewer_reads_github_scoped_to_what_it_needs(tmp_path):
    gh = FakeGh()
    gh.detail = dict(DETAIL, headRefOid="abc123", files=[{"path": "export.py", "additions": 1, "deletions": 0},
                                                       {"path": "logo.png", "additions": 0, "deletions": 0}])
    reviews = Reviews(tmp_path, gh=gh, snapshots=tmp_path / "snapshots", remote="file:///nowhere")

    folder, whole = reviews.checkout("vir/app", 7)  # a repository git can't fetch: only the changed files

    assert not whole and reviews.is_snapshot(str(folder)) and not (tmp_path / "snapshots" / "checkouts").exists()
    assert sorted(p.name for p in folder.rglob("*") if p.is_file()) == [".complete", "export.py"]  # no binary
    assert reviews.checkout("vir/app", 7)[0] == folder and len([c for c in gh.calls if "Blob" in c[-1]]) == 1  # kept
    assert "read_file and list_files read the rest of it on GitHub" in reviews.goal("vir/app", 7, "", whole)
    page = reviews.read_file("vir/app", 7, "export.py", 590)
    assert page.startswith("export.py at abc123, lines 590-599 of 599") and "  599  line 599" in page
    assert "lines 1-400 of 599" in reviews.read_file("vir/app", 7, "export.py") and "ask for start=401" in \
        reviews.read_file("vir/app", 7, "export.py")
    assert "isn't a text file" in reviews.read_file("vir/app", 7, "missing.py")
    assert reviews.list_files("vir/app", 7) == "export.py\nweb/"


async def test_reviewers_get_the_pull_request_on_either_backend_and_only_the_lead_drafts(tmp_path):
    from crewbots.mcp_server import build_review_server
    from crewbots.reviews import ReviewTools

    store = Store(tmp_path / "crew.db")
    claude, codex = FakeBackend(), FakeBackend()
    claude.in_process_tools = True
    runner = Runner(store, make_crew(tmp_path, bot("x")), {"claude": claude, "codex": codex},
                    api_url="http://127.0.0.1:8765/v1")
    reviews = Reviews(tmp_path, gh=FakeGh(), snapshots=tmp_path / "snapshots")
    runner.review_tools = lambda task: ReviewTools(reviews, task.pr, drafts=store, task_id=task.id,
                                                   reads_github=reviews.is_snapshot(task.workspace),
                                                   drafting=task.parent_id is None)
    lead = runner.start_review("reviewer:codex:default:high", title="Review vir/app#7", pr="vir/app#7",
                               request="Review https://github.com/vir/app/pull/7")
    runner.start_reviewers(lead.id, ["reviewer:claude:sonnet:high", "reviewer:codex:default:high"], "Review it",
                           str(tmp_path))
    for _ in range(300):
        if claude.specs and len(codex.specs) == 2:
            break
        await __import__("asyncio").sleep(0.01)

    tools = claude.specs[0].crew
    assert isinstance(tools, ReviewTools) and not claude.specs[0].mcp_servers
    assert [t.name for t in tools.definitions()] == ["pr_overview", "pr_diff"]  # no drafting, no GitHub browsing
    assert "# Add CSV export" in await tools.call("pr_overview", {})
    child, leading = (s.mcp_servers["crewbots"].env for s in codex.specs)
    assert child["CREWBOTS_TOOLS"] == "review" and child["CREWBOTS_REVIEW"] == ""
    assert leading["CREWBOTS_REVIEW"] == "drafts" and codex.specs[1].prompt.startswith("The reviewers have finished")

    stopped = runner.start_review("reviewer:claude:sonnet:high", title="t", pr="vir/app#7", request="Review it")
    await runner.cancel(stopped.id)
    assert runner.start_reviewers(stopped.id, ["reviewer:codex:default:high"], "Review it", str(tmp_path)) == []

    reviewer = {t.name for t in await build_review_server(None, reads_github=False, drafting=False).list_tools()}
    snapshot_lead = {t.name for t in await build_review_server(None, reads_github=True, drafting=True).list_tools()}
    assert reviewer == {"pr_overview", "pr_diff"}
    assert snapshot_lead == {"pr_overview", "pr_diff", "draft_comment", "read_file", "list_files"}
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
