"""Replacing main with a rewritten, same-tree history: the guards, the backup, the swap and what it leaves intact."""

import asyncio
import hashlib
import subprocess
from pathlib import Path

import pytest
from fakes import bot, git_repo, make_crew
from fastapi.testclient import TestClient
from test_push import git
from test_squash_merge import IDENT, SUMMARY

import crewbots.replace_main as replace_main
from crewbots import worktree
from crewbots.api import create_app
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.projects import Project
from crewbots.replace_main import propose
from crewbots.runner import NotAllowed, Runner
from crewbots.store import Status, Store

ADMIN = {"Authorization": "Bearer admin"}
BRANCH = "crewbots/clean-history"


def tree_hashes(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file() and ".git" not in p.relative_to(folder).parts}


def clean_history(repo: Path, name: str = BRANCH) -> str:
    """One commit with main's tree on top of origin/main: the rewritten history."""
    commit = git(repo, *IDENT, "commit-tree", "HEAD^{tree}", "-p", "origin/main", "-m", "Clean history")
    git(repo, "branch", "-f", name, commit)
    return commit


@pytest.fixture
def setup(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-q", str(remote))
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    repo = git_repo(tmp_path / "repo")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    for name in ("one", "two"):
        (repo / f"{name}.txt").write_text(name)
        git(repo, "add", f"{name}.txt")
        git(repo, *IDENT, "commit", "-qm", f"Add {name}")
    store = Store(":memory:")
    crew = make_crew(tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    crew.projects["demo"] = Project("demo", repo, "", tmp_path / "demo.md", ci=("true",))
    runner = Runner(store, crew)
    runner.send = lambda *a, **kw: None
    clean_history(repo)
    with TestClient(create_app(runner, "admin"), base_url="http://127.0.0.1:8765/v1") as client:
        yield runner, client, repo
    store.close()


def make(runner, branch=BRANCH):
    chat = runner.chat("chief")
    proposal = asyncio.run(propose(runner, chat.id, "demo", branch, "Squash for the first push"))
    return proposal, runner.store.last_event(chat.id, ("replace_main_proposed",)).data


def press(client, proposal):
    return client.post(f"/replace-main-proposals/{proposal}/replace", headers=ADMIN).json()


def refused(runner, client, repo, text):
    proposal, data = make(runner)
    assert text in data["error"]
    head, branches = git(repo, "rev-parse", "HEAD"), git(repo, "for-each-ref", "refs/backup")
    result = press(client, proposal)
    assert not result["ok"] and text in result["reason"]
    assert git(repo, "rev-parse", "HEAD") == head and git(repo, "for-each-ref", "refs/backup") == branches == ""


def test_card_data_and_swap(setup):
    runner, client, repo = setup
    files, old = tree_hashes(repo), git(repo, "rev-parse", "main")
    new = git(repo, "rev-parse", BRANCH)
    proposal, data = make(runner)
    assert not data.get("error") and all(c["ok"] for c in data["checks"]) and len(data["checks"]) == 8
    assert (data["old_commits"], data["new_commits"]) == (2, 1)
    assert data["backup"].startswith("refs/backup/main-")
    assert git(repo, "rev-parse", "main") == old  # nothing moves until the button
    result = press(client, proposal)
    assert result["ok"] and result["backup"] == data["backup"]
    assert git(repo, "rev-parse", "main") == new and git(repo, "rev-parse", data["backup"]) == old
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert tree_hashes(repo) == files  # byte-identical
    assert git(repo, "status", "--porcelain") == ""
    assert git(repo, "for-each-ref", "--format=%(refname)").splitlines() == sorted(
        [data["backup"], f"refs/heads/{BRANCH}", "refs/heads/main", "refs/remotes/origin/main"])  # no other ref
    assert client.post(f"/replace-main-proposals/{proposal}/replace", headers=ADMIN).json()["detail"].startswith(
        "This replacement was already started")


def test_backup_name_gets_a_suffix_when_taken(setup):
    runner, client, repo = setup
    first, first_data = make(runner)
    git(repo, "update-ref", first_data["backup"], "HEAD")
    _, second = make(runner)
    assert second["backup"] == first_data["backup"] + "-2"
    git(repo, "update-ref", second["backup"], "HEAD")
    assert make(runner)[1]["backup"] == first_data["backup"] + "-3"


def test_refuses_different_tree(setup):
    runner, client, repo = setup
    git(repo, "checkout", "-q", "-b", "elsewhere", BRANCH)
    (repo / "extra.txt").write_text("x")
    git(repo, "add", "extra.txt")
    git(repo, *IDENT, "commit", "-qm", "Extra")
    git(repo, "branch", "-f", BRANCH, "elsewhere")
    git(repo, "checkout", "-q", "main")
    refused(runner, client, repo, "Same tree")


def test_refuses_when_pushed_history_would_be_rewritten(setup):
    runner, client, repo = setup
    git(repo, "branch", "-f", BRANCH, git(repo, *IDENT, "commit-tree", "HEAD^{tree}", "-m", "Rootless"))
    refused(runner, client, repo, "Pushed history is kept")


def test_refuses_without_an_upstream_ref(setup):
    runner, client, repo = setup
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    git(repo, "branch", "--unset-upstream")
    refused(runner, client, repo, "The target is the pushed branch")


def test_refuses_dirty_folder(setup):
    runner, client, repo = setup
    (repo / "one.txt").write_text("edited")
    refused(runner, client, repo, "Folder is clean")


def test_refuses_merge_in_progress(setup):
    runner, client, repo = setup
    (repo / ".git" / "MERGE_HEAD").write_text(git(repo, "rev-parse", "HEAD") + "\n")
    git(repo, "update-index", "-q", "--refresh")
    refused(runner, client, repo, "No merge in progress")


def test_refuses_while_a_task_is_merging_or_holds_the_folder(setup):
    runner, client, repo = setup
    task = runner.store.create_task("engineer", "merge", status=Status.IDLE)
    runner.store.update_task(task.id, repo=str(repo.resolve()), merge_state="merging")
    refused(runner, client, repo, "No open merge proposals")
    runner.store.update_task(task.id, merge_state=None, base_branch="main", worktree_path=str(repo))
    refused(runner, client, repo, "No merge in progress")


def test_refuses_when_the_target_is_not_checked_out(setup):
    runner, client, repo = setup
    git(repo, "checkout", "-q", "--detach")
    refused(runner, client, repo, "The checked-out branch is the target")
    git(repo, "checkout", "-q", BRANCH)
    refused(runner, client, repo, "The checked-out branch is the target")
    assert git(repo, "rev-parse", "main") != git(repo, "rev-parse", BRANCH)


def advance_remote(tmp_path, remote, name="other"):
    """Another clone pushes a commit to the remote, so origin/main in `repo` is stale."""
    other = tmp_path / name
    git(tmp_path, "clone", "-q", "-b", "main", str(remote), str(other))
    (other / f"{name}.txt").write_text(name)
    git(other, "add", f"{name}.txt")
    git(other, *IDENT, "commit", "-qm", name)
    git(other, "push", "-q", "origin", "main")


def test_refuses_when_the_remote_moved_on_though_origin_main_is_stale(setup, tmp_path):
    runner, client, repo = setup
    advance_remote(tmp_path, tmp_path / "remote.git")
    assert git(repo, "rev-parse", "origin/main") == git(repo, "rev-parse", f"{BRANCH}~1")  # the local ref looks fine
    refused(runner, client, repo, "Pushed history is kept")


def test_card_made_before_the_remote_moved_is_refused_on_press(setup, tmp_path):
    runner, client, repo = setup
    proposal, data = make(runner)
    assert not data.get("error")
    advance_remote(tmp_path, tmp_path / "remote.git")
    result = press(client, proposal)
    assert not result["ok"] and "Pushed history is kept" in result["reason"]
    assert git(repo, "rev-parse", "main") != git(repo, "rev-parse", BRANCH)
    assert git(repo, "for-each-ref", "refs/backup") == ""


def test_the_push_url_is_the_one_checked(setup, tmp_path):
    runner, client, repo = setup
    ahead = tmp_path / "ahead.git"
    git(tmp_path, "clone", "-q", "--bare", str(tmp_path / "remote.git"), str(ahead))
    advance_remote(tmp_path, ahead)  # the push destination has commits the fetch URL doesn't
    git(repo, "config", "remote.origin.pushurl", str(ahead))
    refused(runner, client, repo, "Pushed history is kept")
    # and a dead fetch URL doesn't matter when the push URL is live and holds the history
    git(repo, "config", "remote.origin.pushurl", str(tmp_path / "remote.git"))
    git(repo, "config", "remote.origin.url", str(tmp_path / "nowhere.git"))
    assert not make(runner)[1].get("error")


def test_refuses_when_the_remote_cannot_be_reached(setup, tmp_path):
    runner, client, repo = setup
    git(repo, "config", "remote.origin.pushurl", str(tmp_path / "nowhere.git"))
    refused(runner, client, repo, "live remote could not be read")


def test_refuses_any_branch_but_the_pushed_one(setup):
    runner, client, repo = setup
    git(repo, "checkout", "-q", "-b", "feature", "main")
    git(repo, "branch", "--set-upstream-to", "origin/main")  # a push of it goes to main, not to feature
    refused(runner, client, repo, "The target is the pushed branch")
    git(repo, "branch", "--unset-upstream")
    refused(runner, client, repo, "The target is the pushed branch")


def test_only_the_remotes_default_branch_can_be_replaced(setup, tmp_path):
    runner, client, repo = setup
    remote = tmp_path / "remote.git"
    git(repo, "push", "-q", "origin", "origin/main:refs/heads/feature")
    git(repo, "fetch", "-q", "origin")
    git(repo, "checkout", "-q", "-b", "feature", "main")
    git(repo, "branch", "--set-upstream-to", "origin/feature")  # pushes to feature; every other guard passes
    proposal, data = make(runner)
    assert [c["name"] for c in data["checks"] if not c["ok"]] == ["The target is the remote's default branch"]
    assert press(client, proposal)["ok"] is False
    git(remote, "symbolic-ref", "HEAD", "refs/heads/feature")  # the remote says feature is its default
    assert not make(runner)[1].get("error")


def test_open_proposals_block_it_and_are_listed(setup):
    runner, client, repo = setup
    task = runner.store.create_task("engineer", "open work", status=Status.IDLE)
    runner.store.update_task(task.id, repo=str(repo.resolve()), merge_state="proposed")
    refused(runner, client, repo, "No open merge proposals")
    assert make(runner)[1]["blocking"] == [f"{task.id} (proposed)"]
    runner.store.update_task(task.id, merge_state="merged")
    assert not make(runner)[1].get("error")


def test_stale_card_refuses_after_main_moves(setup):
    runner, client, repo = setup
    proposal, _ = make(runner)
    (repo / "three.txt").write_text("three")
    git(repo, "add", "three.txt")
    git(repo, *IDENT, "commit", "-qm", "Add three")
    result = press(client, proposal)
    assert not result["ok"] and "Same tree" in result["reason"]
    assert git(repo, "for-each-ref", "refs/backup") == ""


def test_compare_and_swap_fails_if_main_moved_after_the_check(setup, monkeypatch):
    runner, client, repo = setup
    proposal, data = make(runner)
    (repo / "three.txt").write_text("three")
    git(repo, "add", "three.txt")
    git(repo, *IDENT, "commit", "-qm", "Add three")
    moved = git(repo, "rev-parse", "main")
    monkeypatch.setattr(replace_main, "snapshot", lambda *a, **kw: {k: data[k] for k in replace_main.KEYS if k in data})
    result = press(client, proposal)
    assert not result["ok"] and "moved" in result["reason"]
    assert git(repo, "rev-parse", "main") == moved
    assert git(repo, "for-each-ref", "refs/backup") == ""  # the backup made for it is taken away again


def test_merged_tasks_are_still_merged_after_the_replacement(setup, tmp_path):
    runner, client, repo = setup
    store = runner.store

    def task_in_worktree(name):
        task = store.create_task("engineer", name, status=Status.IDLE)
        wt = worktree.create(repo, task.id, root=tmp_path / "wts")
        store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base,
                          merge_state="proposed")
        store.append_event(task.id, kind="merge_proposed", data={"by": "chief", "summary": SUMMARY})
        (wt.path / "merged.txt").write_text("merged")
        worktree.commit(wt, "add merged.txt")
        return task, wt

    merged, wt = task_in_worktree("merged")
    copy, _ = task_in_worktree("copy")  # the same change, still proposed
    tip = worktree._git(wt.path, "rev-parse", "HEAD")
    commit = worktree.merge(wt, "Squashed merged", tip=tip)
    store.update_task(merged.id, merge_state="merged", merged_commit=commit, merged_tip=tip)
    assert runner.already_on(store.get_task(copy.id)) == "main"

    store.update_task(copy.id, merge_state=None)  # an open proposal would block the replacement
    clean_history(repo)
    proposal, data = make(runner)
    assert press(client, proposal)["ok"]
    store.update_task(copy.id, merge_state="proposed")
    assert subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "main"]).returncode == 1
    assert store.get_task(merged.id).merged_commit == commit  # records are left as they are

    assert runner.already_on(store.get_task(copy.id)) == "main"  # from the records, not from the backup ref
    swept = runner.sweep_worktrees(dry_run=True)
    assert [r["task_id"] for r in swept["would_remove"]] == [merged.id]  # merged goes; the open proposal stays
    assert runner.drop_worktree(merged.id)["status"] == "removed"  # branch cleanup
    assert store.get_task(merged.id).branch not in git(repo, "branch")
    runner._restore_worktree(store.get_task(merged.id))  # a follow-up reopens the merged work
    assert (Path(store.get_task(merged.id).worktree_path) / "merged.txt").read_text() == "merged"


def test_a_bot_cannot_press_the_button_or_propose_unless_chief(setup):
    runner, client, repo = setup
    runner._tokens["chief-token"] = runner.chat("chief").id
    runner._tokens["eng-token"] = runner.chat("engineer").id
    proposal, _ = make(runner)
    for token in ("chief-token", "eng-token"):
        assert client.post(f"/replace-main-proposals/{proposal}/replace",
                           headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert client.post("/bot/propose_replace_main", headers={"Authorization": "Bearer eng-token"},
                       json={"project": "demo", "branch": BRANCH, "summary": "x"}).status_code == 403
    assert client.post("/bot/propose_replace_main", headers={"Authorization": "Bearer chief-token"},
                       json={"project": "demo", "branch": BRANCH, "summary": "x"}).status_code == 200
    assert git(repo, "rev-parse", "main") != git(repo, "rev-parse", BRANCH)
    with pytest.raises(NotAllowed):
        asyncio.run(propose(runner, runner.chat("engineer").id, "demo", BRANCH, "x"))


def test_tool_is_chief_only(setup):
    runner, _, _ = setup
    chief = CrewTools(runner, runner.chat("chief").id, can_delegate=True)
    assert "propose_replace_main" in {t.name for t in chief.definitions()}
    engineer = CrewTools(runner, runner.chat("engineer").id, can_delegate=False)
    assert "propose_replace_main" not in {t.name for t in engineer.definitions()}
    with pytest.raises(ToolRefused):
        asyncio.run(engineer.call("propose_replace_main", {"project": "demo", "branch": BRANCH, "summary": "x"}))


def test_a_later_reset_of_main_gives_no_false_already_on(setup, tmp_path):
    runner, client, repo = setup
    store = runner.store
    task = store.create_task("engineer", "reverted", status=Status.IDLE)
    wt = worktree.create(repo, task.id, root=tmp_path / "wts")
    (wt.path / "feature.txt").write_text("feature")
    worktree.commit(wt, "add feature")
    tip = git(wt.path, "rev-parse", "HEAD")
    git(repo, *IDENT, "merge", "--no-ff", "-qm", "ordinary merge", tip)
    git(repo, *IDENT, "revert", "--no-edit", "-m", "1", "HEAD")  # the feature is gone from main again
    clean_history(repo)
    proposal, data = make(runner)
    assert press(client, proposal)["ok"]
    store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base,
                      merge_state="proposed")
    # The backup holds the old merge, but it is only a recovery point: it no longer counts as merged.
    git(repo, "merge-base", "--is-ancestor", tip, data["backup"])
    assert runner.already_on(store.get_task(task.id)) is None


def test_a_branch_named_main_old_is_not_main(setup, tmp_path):
    runner, client, repo = setup
    store = runner.store
    task = store.create_task("engineer", "other", status=Status.IDLE)
    wt = worktree.create(repo, task.id, root=tmp_path / "wts")
    (wt.path / "other.txt").write_text("other")
    worktree.commit(wt, "add other")
    tip = git(wt.path, "rev-parse", "HEAD")
    git(repo, "branch", "main-old", tip)  # not merged into main
    git(repo, "update-ref", "refs/backup/main-old", tip)  # nor is a backup-looking ref a record of a merge
    store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base,
                      merge_state="proposed")
    assert runner.already_on(store.get_task(task.id)) is None
    assert runner.already_on(store.get_task(task.id)) is None


def test_refuses_when_the_default_branch_cannot_be_read(setup, monkeypatch):
    runner, client, repo = setup
    real = replace_main._git

    def fail_symref(folder, *args, **kw):
        if "--symref" in args:
            raise NotAllowed("timed out")
        return real(folder, *args, **kw)

    monkeypatch.setattr(replace_main, "_git", fail_symref)
    refused(runner, client, repo, "default branch could not be read")


def legacy_merge(runner, repo, tmp_path):
    """A merged task recorded the old way: a two-parent merge commit and no merged_tip."""
    store = runner.store
    task = store.create_task("engineer", "legacy", status=Status.IDLE)
    wt = worktree.create(repo, task.id, root=tmp_path / "wts")
    (wt.path / "legacy.txt").write_text("legacy")
    worktree.commit(wt, "add legacy")
    tip = git(wt.path, "rev-parse", "HEAD")
    git(repo, *IDENT, "merge", "--no-ff", "-qm", "ordinary merge", tip)
    commit = git(repo, "rev-parse", "HEAD")
    store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base,
                      merge_state="merged", merged_commit=commit, merged_tip=None)
    clean_history(repo)
    return task, tip, commit


def test_a_legacy_merged_record_still_works_after_the_replacement(setup, tmp_path):
    runner, client, repo = setup
    task, tip, commit = legacy_merge(runner, repo, tmp_path)
    proposal, data = make(runner)
    assert press(client, proposal)["ok"]
    now = runner.store.get_task(task.id)
    assert (now.merged_tip, now.merged_commit) == (tip, commit)
    assert runner._merged_tip(now) == tip
    assert "legacy.txt" in runner.changes(task.id)  # diffed
    assert runner.drop_worktree(task.id)["status"] == "removed"  # cleaned up
    runner._restore_worktree(runner.store.get_task(task.id))  # reopened
    assert (Path(runner.store.get_task(task.id).worktree_path) / "legacy.txt").read_text() == "legacy"


def test_a_failed_backfill_refuses_with_main_unmoved(setup, tmp_path, monkeypatch):
    runner, client, repo = setup
    task, tip, commit = legacy_merge(runner, repo, tmp_path)
    proposal, data = make(runner)
    main = git(repo, "rev-parse", "main")
    real = runner.store.update_task

    def broken(task_id, **fields):
        if "merged_tip" in fields:
            raise RuntimeError("disk full")
        return real(task_id, **fields)

    monkeypatch.setattr(runner.store, "update_task", broken)
    result = press(client, proposal)
    assert not result["ok"] and "could not save the merged tip" in result["reason"]
    assert git(repo, "rev-parse", "main") == main and git(repo, "for-each-ref", "refs/backup") == ""
