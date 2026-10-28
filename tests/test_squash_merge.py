"""A task merge is one commit with the proposal's summary; whether a task is merged never depends on ancestry."""

import asyncio
import subprocess

import pytest
from fakes import ScriptedCrew, bot, git_repo
from test_push import git
from test_push import setup as push_setup  # noqa: F401

from crewbots import worktree
from crewbots.push import propose
from crewbots.runner.common import MergeError
from crewbots.runner.merge_message import merge_message
from crewbots.store import Status, Store

SUMMARY = "Add the greeting\n\nThe engineer wrote hello.py.\n\n# Notes\nTested by hand."
IDENT = ("-c", "user.name=t", "-c", "user.email=t@t")


@pytest.fixture
def work(tmp_path):
    """A proposed worktree task with two commits and one uncommitted file."""
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    task = store.create_task("engineer", "add greeting", status=Status.IDLE)
    wt = worktree.create(repo, task.id, root=tmp_path / "wts")
    store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base,
                      merge_state="proposed")
    for name in ("one.txt", "two.txt"):
        (wt.path / name).write_text(name)
        worktree.commit(wt, f"add {name}")
    (wt.path / "loose.txt").write_text("uncommitted")
    store.append_event(task.id, kind="merge_proposed", data={"by": "chief", "summary": SUMMARY})
    yield crew.runner, store, repo, task, wt
    store.close()


def test_merge_makes_one_commit_with_the_summary(work):
    runner, store, repo, task, wt = work
    base = wt.base
    merged = runner.merge(task.id, force=True)
    assert git(repo, "rev-list", "--count", f"{base}..HEAD") == "1"
    assert git(repo, "rev-list", "--parents", "-1", "HEAD").split() == [merged.merged_commit, base]
    assert git(repo, "log", "-1", "--format=%B") == SUMMARY + "\n\nMerged-without-review: yes"
    assert {p.name for p in repo.iterdir()} >= {"one.txt", "two.txt", "loose.txt"}  # uncommitted work is in
    assert merged.merged_tip and merged.merged_tip != merged.merged_commit
    assert merged.branch not in git(repo, "branch")  # cleaned up although the branch is not in main's history
    assert not wt.path.exists()


def test_conflict_changes_nothing(work):
    runner, store, repo, task, wt = work
    (repo / "one.txt").write_text("the user's")
    git(repo, "add", "one.txt")
    git(repo, *IDENT, "commit", "-qm", "user edit")
    head = git(repo, "rev-parse", "HEAD")
    with pytest.raises(MergeError, match="nothing was changed"):
        runner.merge(task.id, force=True)
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / "one.txt").read_text() == "the user's"
    assert not (repo / ".git" / "SQUASH_MSG").exists()
    assert store.get_task(task.id).merge_state == "proposed"


def squash_merged(runner, store, repo, task, wt):
    """What the merge leaves, but with the checkout still there: a squash commit and the recorded tip."""
    worktree.commit(wt, "last")
    tip = git(wt.path, "rev-parse", "HEAD")
    commit = worktree.merge(wt, "Squashed", tip=tip)
    store.update_task(task.id, merge_state="merged", merged_commit=commit, merged_tip=tip)
    return store.get_task(task.id)


def test_squash_merged_task_is_removed_by_sweep_and_drop(work):
    runner, store, repo, task, wt = work
    merged = squash_merged(runner, store, repo, task, wt)
    assert subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", merged.merged_tip, "HEAD"]).returncode
    entry = runner.sweep_worktrees(dry_run=True)["would_remove"][0]
    assert entry["reason"] == "merged and clean"
    assert runner.drop_worktree(task.id)["branch_status"] == "deleted"  # the recorded tip, not ancestry, says merged
    assert not wt.path.exists() and wt.branch not in git(repo, "branch")


def test_sweep_keeps_a_squash_merged_branch_with_later_commits(work):
    runner, store, repo, task, wt = work
    squash_merged(runner, store, repo, task, wt)
    (wt.path / "later.txt").write_text("later")
    worktree.commit(wt, "later")
    assert runner.drop_worktree(task.id)["status"] == "kept"  # not within what was merged
    assert wt.path.exists()
    assert runner.sweep_worktrees()["removed"][0]["branch_status"] == "kept"
    assert git(repo, "show", f"{wt.branch}:later.txt") == "later"


def test_follow_up_restores_a_squash_merged_task(work):
    runner, store, repo, task, wt = work
    merged = squash_merged(runner, store, repo, task, wt)
    worktree.remove(wt, delete_branch=True)
    runner._restore_worktree(merged)
    assert git(wt.path, "rev-parse", "HEAD") == merged.merged_tip
    assert runner.changes(task.id) == worktree._git(repo, "diff", wt.base, merged.merged_tip, strip=False)


def test_already_on_reports_a_squash_merge(work):
    runner, store, repo, task, wt = work
    worktree.commit(wt, "last")
    worktree.merge(wt, "Squashed elsewhere", tip=git(wt.path, "rev-parse", "HEAD"))
    assert runner.already_on(store.get_task(task.id)) == "main"
    with pytest.raises(MergeError, match="already on main"):
        runner.merge(task.id, force=True)


def test_message_is_subject_blank_line_body():
    assert merge_message(SUMMARY, "t") == SUMMARY
    assert merge_message(SUMMARY, "t", unreviewed=True) == SUMMARY + "\n\nMerged-without-review: yes"
    assert merge_message("", "Title", unreviewed=True) == "Title\n\nMerged-without-review: yes"
    # A subject made from the title is never stripped from the summary.
    fixation = ("Fixation of the rounding " + "detail " * 12).strip()
    assert merge_message(fixation, "Fix") == f"Fix\n\n{fixation}"
    assert merge_message("Just a subject", "t") == "Just a subject"
    sentence = "Fix the update dialog. " + "More detail " * 8
    assert merge_message(sentence, "t").splitlines()[0] == "Fix the update dialog."
    assert merge_message(sentence, "t").split("\n\n", 1)[1] == sentence.split(". ", 1)[1].strip()


def test_push_card_lists_exactly_what_would_be_sent(push_setup):  # noqa: F811
    runner, client, repo, remote, notices = push_setup
    # One old-style pair: the task's commit and a --no-ff merge commit with the same subject.
    git(repo, "checkout", "-qb", "old")
    (repo / "old.txt").write_text("old")
    git(repo, "add", "old.txt")
    git(repo, *IDENT, "commit", "-qm", "Old style")
    git(repo, "checkout", "-q", "main")
    git(repo, *IDENT, "merge", "--no-ff", "-qm", "Old style", "old")
    # One squash commit.
    git(repo, "checkout", "-qb", "new")
    (repo / "new2.txt").write_text("new")
    git(repo, "add", "new2.txt")
    git(repo, *IDENT, "commit", "-qm", "Task commit")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--squash", "new")
    git(repo, *IDENT, "commit", "-qm", "New style")
    chat = runner.chat("chief")
    asyncio.run(propose(runner, chat.id, "demo", "Ready"))
    commits = runner.store.last_event(chat.id, ("push_proposed",)).data["commits"]
    tip = git(remote, "rev-parse", "refs/heads/main")
    assert commits == git(repo, "log", "--format=%H %s", f"{tip}..HEAD").splitlines()
    assert len(commits) == len(set(commits)) == int(git(repo, "rev-list", "--count", f"{tip}..HEAD"))
    assert sorted(c.split(" ", 1)[1] for c in commits) == ["Add one", "New style", "Old style", "Old style"]


def test_records_of_old_merge_commits_still_find_their_tip(work):
    runner, store, repo, task, wt = work
    worktree.commit(wt, "last")
    tip = git(wt.path, "rev-parse", "HEAD")
    git(repo, *IDENT, "merge", "--no-ff", "-qm", "old merge", tip)
    store.update_task(task.id, merge_state="merged", merged_commit=git(repo, "rev-parse", "HEAD"), merged_tip=None)
    assert runner._merged_tip(store.get_task(task.id)) == tip
    assert runner.drop_worktree(task.id)["branch_status"] == "deleted"  # ancestry of the merge commit, as before


def test_forced_merge_has_the_trailer_and_approved_does_not(work):
    runner, store, repo, task, wt = work
    runner.merge(task.id, force=True)  # nothing approved this work
    assert git(repo, "log", "-1", "--format=%B") == SUMMARY + "\n\nMerged-without-review: yes"


def test_approved_merge_has_no_trailer(work, monkeypatch):
    from types import SimpleNamespace
    runner, store, repo, task, wt = work
    monkeypatch.setattr(runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, rounds=[1],
                                                                          version=1, latest=[]))
    runner.merge(task.id)
    assert git(repo, "log", "-1", "--format=%B") == SUMMARY


def test_sweep_preview_shows_commits_after_the_merge(work):
    runner, store, repo, task, wt = work
    squash_merged(runner, store, repo, task, wt)
    (wt.path / "later.txt").write_text("later")
    worktree.commit(wt, "later")
    assert runner.sweep_worktrees(dry_run=True)["would_remove"][0]["reason"] == (
        "merged, 1 later commit(s) kept on the branch")
