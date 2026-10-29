"""Cleanup uses real Git repositories; no user's checkout is involved."""

import asyncio
import threading
from pathlib import Path

import httpx
import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until

from crewbots import worktree
from crewbots.api import create_app
from crewbots.backends import Access
from crewbots.crew_tools import CrewTools
from crewbots.runner.common import NotAllowed
from crewbots.store import Status, Store


def setup_task(store, repo, root, status=Status.IDLE, merge_state=None):
    task = store.create_task("engineer", "work", status=status)
    wt = worktree.create(repo, task.id, root=root)
    store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base,
                      merge_state=merge_state)
    return store.get_task(task.id), wt


def test_sweep_saves_changes_and_reports_all_worktrees(tmp_path):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    dropped, wt = setup_task(store, repo, tmp_path / "wts", Status.CANCELLED)
    running, live = setup_task(store, repo, tmp_path / "wts", Status.RUNNING)
    proposed, pending = setup_task(store, repo, tmp_path / "wts", merge_state="proposed")
    (wt.path / "README.md").write_text("changed")
    (wt.path / "new.txt").write_text("new")
    (wt.path / ".gitignore").write_text("ignored.txt\n")
    (wt.path / "ignored.txt").write_text("ignored")
    summary = runner.sweep_worktrees()
    report = {entry["task_id"]: entry for entry in summary["removed"] + summary["kept"]}
    assert report[dropped.id]["status"] == "removed"
    assert report[dropped.id]["branch_status"] == "kept"
    assert report[running.id]["status"] == report[proposed.id]["status"] == "kept"
    assert live.path.is_dir() and pending.path.is_dir() and not wt.path.exists()
    assert worktree._git(repo, "show", f"{wt.branch}:new.txt") == "new"
    assert worktree._git(repo, "show", f"{wt.branch}:README.md") == "changed"
    assert "ignored.txt" not in worktree._git(repo, "ls-tree", "-r", "--name-only", wt.branch).splitlines()
    assert worktree._git(repo, "log", "-1", "--format=%s", wt.branch) == (
        f"Uncommitted work from {dropped.id}, saved on removal")
    store.close()


@pytest.mark.parametrize("staged", [False, True])
def test_nested_untracked_repository_is_left_in_place(tmp_path, staged):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts")
    nested = git_repo(wt.path / "nested")
    (nested / "untracked.txt").write_text("not committed")
    if staged:
        worktree.changes(wt)  # the diff endpoint stages files, including an embedded repository as a gitlink
    report = runner.sweep_worktrees()
    assert not report["removed"] and report["kept"][0]["task_id"] == task.id
    assert "nested repository" in report["kept"][0]["reason"]
    assert (nested / "untracked.txt").read_text() == "not committed"
    assert worktree._git(nested, "rev-parse", "HEAD")
    store.close()


def test_failure_is_logged_and_worktree_kept(tmp_path, monkeypatch, caplog):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts")
    (wt.path / "new.txt").write_text("safe")
    original = worktree._git

    def fail(cwd, *args, **kwargs):
        if args[:1] == ("add",):
            raise worktree.GitError("simulated failure")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", fail)
    result = runner.drop_worktree(task.id)
    assert result["status"] == "kept" and "simulated failure" in caplog.text
    assert Path(wt.path, "new.txt").read_text() == "safe"
    assert original(repo, "rev-parse", wt.branch)
    store.close()


def test_sweep_keeps_merged_branch_with_later_commits(tmp_path):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts", merge_state="merged")
    store.update_task(task.id, merged_commit=worktree._git(repo, "rev-parse", "HEAD"))
    (wt.path / "later.txt").write_text("later work")
    worktree.commit(wt, "later work")
    report = runner.sweep_worktrees()
    assert report["removed"][0]["branch_status"] == "kept"
    assert not wt.path.exists()
    assert worktree._git(repo, "show", f"{wt.branch}:later.txt") == "later work"
    store.close()


def test_prune_failure_logs_after_removal_and_keeps_saved_work(tmp_path, monkeypatch, caplog):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts")
    (wt.path / "new.txt").write_text("safe")
    original = worktree._git

    def fail(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "prune"):
            raise worktree.GitError("prune failed")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", fail)
    assert runner.drop_worktree(task.id)["status"] == "removed"
    assert not wt.path.exists()
    assert original(repo, "show", f"{wt.branch}:new.txt") == "safe"
    assert "prune failed" in caplog.text
    store.close()


def test_branch_delete_failure_is_logged_and_prune_still_runs(tmp_path, monkeypatch, caplog):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts", merge_state="merged")
    store.update_task(task.id, merged_commit=worktree._git(repo, "rev-parse", "HEAD"))
    original = worktree._git
    commands = []

    def fail(cwd, *args, **kwargs):
        commands.append(args)
        if args[:2] == ("update-ref", "-d"):
            raise worktree.GitError("branch deletion failed")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", fail)
    report = runner.drop_worktree(task.id)
    assert report["status"] == "removed" and report["branch_status"] == "kept"
    assert not wt.path.exists() and original(repo, "rev-parse", wt.branch)
    assert ("worktree", "prune") in commands
    assert "branch deletion failed" in caplog.text
    store.close()


def test_merged_branch_advancing_before_removal_is_kept(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts", merge_state="merged")
    store.update_task(task.id, merged_commit=worktree._git(repo, "rev-parse", "HEAD"))
    original = worktree._git
    advanced = False

    def advance(cwd, *args, **kwargs):
        nonlocal advanced
        if args == ("rev-parse", f"refs/heads/{wt.branch}") and not advanced:
            advanced = True
            (wt.path / "later.txt").write_text("later work")
            worktree.commit(wt, "later work")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", advance)
    assert runner.drop_worktree(task.id)["status"] == "kept"
    assert wt.path.is_dir()
    assert original(repo, "show", f"{wt.branch}:later.txt") == "later work"
    store.close()


def test_partial_remove_failure_keeps_saved_branch(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts")
    (wt.path / "README.md").write_text("edited")
    (wt.path / "new.txt").write_text("new")
    original = worktree._git

    def fail(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            (wt.path / "README.md").unlink()  # a removal which failed partway through
            (wt.path / "new.txt").unlink()
            raise worktree.GitError("partial removal")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", fail)
    assert runner.drop_worktree(task.id)["status"] == "kept"
    assert original(repo, "show", f"{wt.branch}:README.md") == "edited"
    assert original(repo, "show", f"{wt.branch}:new.txt") == "new"
    store.close()


def test_failure_before_removal_preserves_checkout_and_changes(tmp_path, monkeypatch, caplog):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts")
    (wt.path / "README.md").write_text("changed")
    (wt.path / "new.txt").write_text("untracked")
    original = worktree._git
    before = original(wt.path, "rev-parse", "HEAD")
    status = original(wt.path, "status", "--porcelain")

    def fail(cwd, *args, **kwargs):
        if "--ignore-submodules=none" in args:
            raise worktree.GitError("status failed")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", fail)
    assert runner.drop_worktree(task.id)["status"] == "kept"
    assert original(wt.path, "rev-parse", "HEAD") == before
    assert original(wt.path, "status", "--porcelain") == status
    assert (wt.path / "README.md").read_text() == "changed"
    assert (wt.path / "new.txt").read_text() == "untracked"
    assert "status failed" in caplog.text
    store.close()


def test_remaining_dirty_state_prevents_forced_removal(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts")
    original = worktree._git

    def dirty(cwd, *args, **kwargs):
        if "--ignore-submodules=none" in args:
            return " M nested"
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", dirty)
    assert runner.drop_worktree(task.id)["status"] == "kept"
    assert wt.path.is_dir()
    store.close()


@pytest.mark.parametrize("status", [Status.RUNNING, Status.QUEUED, Status.HELD, Status.NEEDS_INPUT, Status.INTERRUPTED])
def test_sweep_skips_unfinished_tasks(tmp_path, status):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts", status)
    (wt.path / "new").write_text("still working")
    report = runner.sweep_worktrees()
    assert report["removed"] == [] and report["kept"][0]["task_id"] == task.id
    assert (wt.path / "new").read_text() == "still working"
    store.close()


def test_failed_remove_keeps_work_and_branch(tmp_path, monkeypatch, caplog):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("engineer")).runner
    task, wt = setup_task(store, repo, tmp_path / "wts")
    original = worktree._git
    (wt.path / "README.md").write_text("changed")
    (wt.path / "new").write_text("untracked")

    def fail(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            raise worktree.GitError("remove failed")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", fail)
    assert runner.drop_worktree(task.id)["status"] == "kept"
    assert wt.path.is_dir()
    assert (wt.path / "new").read_text() == "untracked"
    assert original(repo, "show", f"{wt.branch}:new") == "untracked"
    assert original(repo, "show", f"{wt.branch}:README.md") == "changed"
    assert "remove failed" in caplog.text
    store.close()


async def test_drop_saves_work_and_follow_up_restores_checkout(tmp_path):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    crew = ScriptedCrew(store, tmp_path, bot("engineer", access=Access.WORKSPACE_WRITE))
    task = crew.runner.submit("engineer", "work", workspace=str(repo))
    await wait_until(crew.settled(task.id))
    task = store.get_task(task.id)
    path = Path(task.worktree_path)
    (path / "new.txt").write_text("saved")
    report = crew.runner.drop_worktree(task.id)
    assert report["status"] == "removed" and report["branch_status"] == "kept"
    assert not path.exists() and worktree._git(repo, "show", f"{task.branch}:new.txt") == "saved"
    assert "new.txt" in crew.runner.changes(task.id)
    crew.runner.send(task.id, "continue")
    await wait_until(crew.settled(task.id))
    assert (path / "new.txt").read_text() == "saved"
    store.close()


async def test_sweep_runs_off_loop_and_waits_to_release_tasks_on_request_cancel(tmp_path, monkeypatch):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    crew = ScriptedCrew(store, tmp_path, bot("engineer", access=Access.WORKSPACE_WRITE))
    task = crew.runner.submit("engineer", "work", workspace=str(repo))
    await wait_until(crew.settled(task.id))
    started, finish = threading.Event(), threading.Event()
    original = crew.runner.sweep_worktrees

    def slow_sweep():
        started.set()
        if not finish.wait(5):
            raise TimeoutError("test did not release sweep")
        return original()

    monkeypatch.setattr(crew.runner, "sweep_worktrees", slow_sweep)
    app = create_app(crew.runner, token="admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765/v1") as client:
        request = asyncio.create_task(client.post("/worktrees/sweep", headers={"Authorization": "Bearer admin"}))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            assert (await client.get("/bots", headers={"Authorization": "Bearer admin"})).status_code == 200
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert not crew.runner._worktree_sweep_done.is_set()
            crew.runner.send(task.id, "continue")
            await asyncio.sleep(0.02)
            assert store.get_task(task.id).status == Status.QUEUED
        finally:
            finish.set()
        await wait_until(crew.runner._worktree_sweep_done.is_set)
        await wait_until(crew.settled(task.id))
        assert store.get_task(task.id).status == Status.IDLE
    store.close()


def on_own_branch(store, repo, tmp_path, name="crewbots/t_own", **fields):
    """A merged task whose engineer worked on a branch of their own naming, squash-merged into main since."""
    task, wt = setup_task(store, repo, tmp_path / "wts", merge_state="merged")
    worktree._git(wt.path, "switch", "-c", name)
    (wt.path / "work.txt").write_text("work")
    worktree.commit(wt, "work")
    worktree._git(repo, "merge", "--squash", name)
    worktree._git(repo, "commit", "-m", "squashed")
    store.update_task(task.id, merged_commit=worktree._git(repo, "rev-parse", "HEAD"), **fields)
    return store.get_task(task.id), wt, name


def squash_setup(tmp_path):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    return repo, store, ScriptedCrew(store, tmp_path, bot("engineer")).runner


def test_sweep_removes_a_merged_worktree_on_another_crewbots_branch_and_keeps_the_branch(tmp_path):
    repo, store, runner = squash_setup(tmp_path)
    task, wt, name = on_own_branch(store, repo, tmp_path)
    assert worktree._git(repo, "show", "HEAD:work.txt") == "work"
    dry = runner.sweep_worktrees(dry_run=True)
    assert dry["would_remove"][0]["task_id"] == task.id and "already on" in dry["would_remove"][0]["reason"]
    assert wt.path.is_dir()
    assert runner.sweep_worktrees()["removed"][0]["task_id"] == task.id
    assert not wt.path.exists()
    assert worktree._git(repo, "rev-parse", "--verify", f"refs/heads/{name}")
    store.close()


def test_sweep_keeps_a_worktree_on_another_branch_with_unmerged_changes(tmp_path):
    repo, store, runner = squash_setup(tmp_path)
    task, wt, name = on_own_branch(store, repo, tmp_path)
    (wt.path / "more.txt").write_text("more")
    worktree.commit(wt, "more")
    for dry in (True, False):
        kept = runner.sweep_worktrees(dry_run=dry)["kept"]
        assert kept[0]["task_id"] == task.id and "holds changes not on" in kept[0]["reason"]
    assert wt.path.is_dir()
    store.close()


def test_sweep_keeps_a_dirty_worktree_on_another_branch(tmp_path):
    repo, store, runner = squash_setup(tmp_path)
    task, wt, name = on_own_branch(store, repo, tmp_path)
    (wt.path / "scratch.txt").write_text("not saved")
    kept = runner.sweep_worktrees()["kept"]
    assert kept[0]["task_id"] == task.id and "uncommitted changes" in kept[0]["reason"]
    assert (wt.path / "scratch.txt").exists()
    store.close()


def test_sweep_removes_an_unmerged_task_whose_work_is_all_on_main(tmp_path):
    repo, store, runner = squash_setup(tmp_path)
    task, wt, name = on_own_branch(store, repo, tmp_path, merge_state=None)
    assert runner.sweep_worktrees(dry_run=True)["would_remove"][0]["task_id"] == task.id
    assert runner.sweep_worktrees()["removed"][0]["task_id"] == task.id
    assert not wt.path.exists()
    assert worktree._git(repo, "rev-parse", "--verify", f"refs/heads/{name}")
    store.close()


def test_sweep_keeps_a_branch_matching_an_old_main_commit_but_not_the_current_one(tmp_path):
    repo, store, runner = squash_setup(tmp_path)
    task, wt, name = on_own_branch(store, repo, tmp_path)
    (repo / "work.txt").write_text("later work")  # main moves on from what the branch holds
    worktree._git(repo, "commit", "-am", "later")
    kept = runner.sweep_worktrees()["kept"]
    assert kept[0]["task_id"] == task.id and "holds changes not on" in kept[0]["reason"]
    assert wt.path.is_dir()
    store.close()


async def test_handing_back_on_the_wrong_branch_is_told_to_switch_and_cannot_be_proposed(tmp_path):
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    runner = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer")).runner
    parent = store.create_task("chief", "lead", status=Status.IDLE)
    work, wt = setup_task(store, repo, tmp_path / "wts")
    store.update_task(work.id, parent_id=parent.id)
    assert runner.wrong_branch(store.get_task(work.id)) is None
    worktree._git(wt.path, "switch", "-c", "crewbots/t_own")
    note = runner.wrong_branch(store.get_task(work.id))
    assert f"git switch {wt.branch}" in note and "crewbots/t_own" in note
    assert f"git switch {wt.branch}" in runner._checked(store.get_task(work.id), "Done.")
    assert f"git switch {wt.branch}" in await CrewTools(runner, work.id, can_delegate=False).call(
        "handoff", {"done": "x", "evidence": ["pytest -q: 3 passed"]})
    with pytest.raises(NotAllowed, match="cannot be proposed"):
        runner.propose_merge(parent.id, work.id, "Ready")
    worktree._git(wt.path, "switch", "--detach")
    assert "detached HEAD" in runner.wrong_branch(store.get_task(work.id))
    worktree._git(wt.path, "switch", wt.branch)
    assert runner.wrong_branch(store.get_task(work.id)) is None
    store.close()
