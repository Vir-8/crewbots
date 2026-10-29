"""Code work isn't handed back with comments that excuse or defer: the cause gets fixed, or what's open goes in the
handoff. A comment stating an outside constraint with its issue link stays."""

import pytest
from fakes import ScriptedCrew, bot, git_repo

from crewbots import comments, worktree
from crewbots.backends import Access
from crewbots.runner import NotAllowed
from crewbots.store import Status, Store

DIFF = """diff --git a/Assets/Glider.cs b/Assets/Glider.cs
--- a/Assets/Glider.cs
+++ b/Assets/Glider.cs
@@ -10,2 +10,7 @@
 void Update() {
+    // Clamp so the glider never stalls below its minimum speed.
+    speed = Mathf.Max(speed, minSpeed); // TODO: tune this later
+    // Unity bug https://issuetracker.unity3d.com/issues/12345: workaround for wind zones
+    Log("for now, no lift");
-    old();
+    // Temporary hack until the reviewer is happy
 }
--- a/notes.md
+++ b/notes.md
@@ -1 +1,2 @@
+TODO: write the guide
+# for now, nothing
"""


def test_the_lines_that_excuse_or_defer_in_code_are_found():
    assert comments.excuses(DIFF) == [
        "Assets/Glider.cs:12: speed = Mathf.Max(speed, minSpeed); // TODO: tune this later",
        "Assets/Glider.cs:15: // Temporary hack until the reviewer is happy",
    ]  # not the plain comment, the linked constraint, a string, or a Markdown file


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def test_a_worktree_handback_with_an_excuse_comment_is_refused_until_its_fixed(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path / "crew", bot("engineer", access=Access.WORKSPACE_WRITE))
    repo = git_repo(tmp_path / "game")
    task = store.create_task("engineer", "fix the glide", status=Status.IDLE)
    wt = worktree.create(repo, task.id, root=tmp_path / "wts")
    store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base)
    (wt.path / "glide.py").write_text("def glide(speed):\n    return max(speed, 2)  # quick fix for the stall\n")

    with pytest.raises(NotAllowed, match=r"glide.py:2: return max\(speed, 2\)  # quick fix for the stall"):
        crew.runner.handoff(task.id, "Fixed the stall", [], "", "", ["ran it"])
    (wt.path / "glide.py").write_text("def glide(speed):\n    return max(speed, MIN_SPEED)\n")
    assert crew.runner.handoff(task.id, "Fixed the stall", [], "", "", ["ran it"])


def test_in_a_shared_folder_only_the_works_own_files_are_read(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path / "crew", bot("engineer", access=Access.WORKSPACE_WRITE))
    repo = git_repo(tmp_path / "game")
    (repo / "theirs.py").write_text("x = 1  # TODO: someone else's, not ours\n")
    (repo / "mine.py").write_text("def lift():\n    pass  # HACK: lift later\n")
    task = store.create_task("engineer", "add lift", workspace=str(repo), status=Status.IDLE)

    with pytest.raises(NotAllowed, match="mine.py:2"):
        crew.runner.handoff(task.id, "Added lift", ["mine.py"], "", "", ["ran it"])
    (repo / "mine.py").write_text("def lift():\n    return 1\n")
    assert crew.runner.handoff(task.id, "Added lift", ["mine.py"], "", "", ["ran it"])
