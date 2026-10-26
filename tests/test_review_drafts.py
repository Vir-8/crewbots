"""Line comments for a review: drafted by the reviewers or the user, private until the user submits a review, which
only the user can do."""

import base64
import json

import pytest
from fakes import FakeBackend, make_crew
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.crew_tools import ToolRefused
from crewbots.reviews import ReviewError, Reviews, ReviewTools, commentable
from crewbots.runner import Runner
from crewbots.store import Store

DIFF = """diff --git a/export.py b/export.py
index 1111111..2222222 100644
--- a/export.py
+++ b/export.py
@@ -10,4 +10,5 @@ def rows():
     header = ["name"]
-    return header
+    out = list(header)
+    return out
{blank}
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-gone = 1
-also = 2
diff --git a/logo.png b/logo.png
new file mode 100644
index 0000000..a8a5ce8
Binary files /dev/null and b/logo.png differ
""".format(blank=" ")  # an unchanged blank line, which git writes as a single space


def test_only_lines_the_diff_shows_can_take_a_comment():
    lines = commentable(DIFF)

    assert lines["export.py"] == {"RIGHT": {10, 11, 12, 13}, "LEFT": {10, 11, 12}}
    assert lines["old.py"] == {"RIGHT": set(), "LEFT": {1, 2}}
    assert "logo.png" not in lines  # a binary file has no lines


class Gh:
    def __init__(self):
        self.posted: list[dict] = []

    def __call__(self, args, stdin=None):
        if args[:2] == ["pr", "view"]:
            return json.dumps({"number": 7, "title": "Rows", "body": "", "url": "https://github.com/vir/app/pull/7",
                               "state": "OPEN", "isDraft": False, "author": {"login": "sam"}, "headRefName": "rows",
                               "baseRefName": "main", "headRefOid": "abc123", "baseRefOid": "def456", "additions": 3,
                               "deletions": 3, "changedFiles": 3, "files": [], "reviews": [], "comments": [],
                               "labels": [], "statusCheckRollup": [], "closingIssuesReferences": []})
        if args[:2] == ["pr", "diff"]:
            return DIFF
        if args[:4] == ["api", "-X", "POST", "repos/vir/app/pulls/7/reviews"]:
            self.posted.append(json.loads(stdin))
            return json.dumps({"html_url": "https://github.com/vir/app/pull/7#pullrequestreview-1", "state": "APPROVED"})
        if args[0] == "api" and "contents/logo.png?ref=abc123" in args[1]:
            return json.dumps({"content": base64.b64encode(b"\x89PNG").decode(), "sha": "f00"})
        raise AssertionError(f"gh {args} isn't expected")


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "crew.db")
    gh = Gh()
    reviews = Reviews(tmp_path, gh=gh)
    yield store, gh, reviews
    store.close()


async def test_a_reviewer_drafts_a_comment_on_a_line_the_diff_shows(world):
    store, _, reviews = world
    tools = ReviewTools(reviews, "vir/app#7", drafts=store, task_id="t_r1", author="reviewer:claude:sonnet:high")

    said = await tools.call("draft_comment", {"path": "export.py", "line": 11, "body": "`out` shadows the builtin."})

    assert said.startswith("Drafted comment 1 on export.py:11.")
    draft = store.drafts("vir/app", 7)[0]
    assert (draft["side"], draft["author"], draft["task_id"], draft["commit_sha"]) == (
        "RIGHT", "reviewer:claude:sonnet:high", "t_r1", "abc123")
    with pytest.raises(ToolRefused, match="line 40 of export.py isn't in the diff"):
        await tools.call("draft_comment", {"path": "export.py", "line": 40, "body": "x"})
    with pytest.raises(ToolRefused, match="isn't one of this pull request's changed files"):
        await tools.call("draft_comment", {"path": "elsewhere.py", "line": 1, "body": "x"})
    assert await tools.call("draft_comment", {"path": "old.py", "line": 2, "side": "LEFT", "body": "why remove this?"})


async def test_only_the_lead_drafts_and_only_without_the_repository_does_a_reviewer_read_github(world):
    store, _, reviews = world
    lead = ReviewTools(reviews, "vir/app#7", drafts=store)
    reviewer = ReviewTools(reviews, "vir/app#7", drafts=store, drafting=False)

    assert [t.name for t in lead.definitions()] == ["pr_overview", "pr_diff", "draft_comment"]
    assert [t.name for t in reviewer.definitions()] == ["pr_overview", "pr_diff"]
    with pytest.raises(ToolRefused, match="not one of the reviewer's tools"):
        await lead.call("read_file", {"path": "export.py"})
    with pytest.raises(ToolRefused, match="not one of the reviewer's tools"):
        await reviewer.call("draft_comment", {"path": "export.py", "line": 12, "body": "x"})
    assert store.drafts("vir/app", 7) == []
    assert not any("submit" in t.name or "post" in t.name for t in ReviewTools(reviews, "vir/app#7").definitions())


def test_the_user_edits_drafts_and_submits_the_review_and_no_bot_can(world, tmp_path):
    store, gh, reviews = world
    runner = Runner(store, make_crew(tmp_path), {"fake": FakeBackend()}, worktree_root=tmp_path / "wts")
    auth = {"Authorization": "Bearer t"}
    with TestClient(create_app(runner, "t", 8765, reviews=reviews), base_url="http://127.0.0.1:8765") as client:
        base = "/v1/reviews/vir/app/7"
        mine = client.post(f"{base}/drafts", headers=auth, json={"path": "export.py", "line": 12, "body": "Nit"}).json()
        assert mine["author"] == "user"
        assert client.post(f"{base}/drafts", headers=auth, json={"path": "export.py", "line": 99, "body": "x"}).status_code == 422
        store.add_draft("vir/app", 7, "export.py", 11, "RIGHT", "From a reviewer", "reviewer:codex:default:high")
        edited = client.put(f"/v1/reviews/drafts/{mine['id']}", headers=auth, json={"body": "Rename this"}).json()
        assert edited["body"] == "Rename this"

        # A reviewer's token reaches only /v1/bot: it can't submit, or post anything.
        task = store.create_task("reviewer:claude:sonnet:high", "Review", kind="review", pr="vir/app#7")
        token = "bot-token"
        runner._tokens[token] = task.id
        assert client.post(f"{base}/submit", headers={"Authorization": f"Bearer {token}"},
                           json={"event": "APPROVE"}).status_code == 403
        assert gh.posted == []

        done = client.post(f"{base}/submit", headers=auth, json={"event": "APPROVE", "body": "Looks good."}).json()
        assert done == {"url": "https://github.com/vir/app/pull/7#pullrequestreview-1", "state": "APPROVED", "comments": 2}
        assert gh.posted == [{"commit_id": "abc123", "event": "APPROVE", "body": "Looks good.", "comments": [
            {"path": "export.py", "line": 11, "side": "RIGHT", "body": "From a reviewer"},
            {"path": "export.py", "line": 12, "side": "RIGHT", "body": "Rename this"}]}]
        assert client.get(f"{base}/drafts", headers=auth).json() == []  # submitted, so no longer drafts


def test_a_review_that_doesnt_approve_needs_something_to_say(world):
    _, _, reviews = world

    with pytest.raises(ReviewError, match="needs a comment"):
        reviews.submit("vir/app", 7, "COMMENT", "", [])
    with pytest.raises(ReviewError, match="saying what to change"):
        reviews.submit("vir/app", 7, "REQUEST_CHANGES", "", [{"path": "a", "line": 1, "side": "RIGHT", "body": "x"}])


def test_an_image_the_pull_request_adds_comes_from_its_head(world):
    _, _, reviews = world

    image = reviews.image("vir/app", 7, "logo.png", "head")

    assert image["mime"] == "image/png" and base64.b64decode(image["data"]) == b"\x89PNG"
    with pytest.raises(ReviewError, match="isn't an image"):
        reviews.image("vir/app", 7, "export.py", "head")
