import asyncio
import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from fakes import ScriptedCrew, bot, wait_until
from fastapi.testclient import TestClient

from crewbots import branch_mode, paths, projects, worktree
from crewbots.api import create_app
from crewbots.backends import Access
from crewbots.crew_tools import CrewTools
from crewbots.runner.common import MergeError, NotAllowed
from crewbots.store import Status, Store


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run([paths.git(), *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "main")
    (repo / ".gitignore").write_text("Temp/\n")
    (repo / "file").write_text("original\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "initial")
    return repo, git


def test_start_keeps_files_with_unity_lockfile(repo):
    path, git = repo
    (path / "Temp").mkdir()
    (path / "Temp" / "UnityLockfile").write_text("locked")
    before = git("rev-parse", "HEAD")
    checkout = branch_mode.start(path, "task-1")
    assert checkout.original_branch == "main" and checkout.base == before
    assert git("branch", "--show-current") == "crewbots/task-1"
    assert (path / "file").read_text() == "original\n"
    assert (path / "Temp" / "UnityLockfile").read_text() == "locked"


def test_unignored_unity_lockfile_does_not_block_start(repo):
    path, git = repo
    (path / ".gitignore").write_text("")
    git("add", ".gitignore")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "track config")
    (path / "Temp").mkdir()
    (path / "Temp" / "UnityLockfile").touch()
    checkout = branch_mode.start(path, "task-1")
    assert checkout.branch == "crewbots/task-1"
    assert branch_mode.changed_files(checkout) == []


def test_start_refuses_project_inside_a_larger_repository(repo):
    path, git = repo
    nested = path / "game"
    nested.mkdir()
    with pytest.raises(worktree.GitError, match="not the repository root"):
        branch_mode.start(nested, "task-1")
    assert git("branch", "--show-current") == "main"


@pytest.mark.parametrize("dirty", ["file", "new"])
def test_dirty_project_is_not_switched(repo, dirty):
    path, git = repo
    (path / dirty).write_text("unsaved work")
    with pytest.raises(worktree.GitError, match="uncommitted or untracked"):
        branch_mode.start(path, "task-1")
    assert git("branch", "--show-current") == "main"
    assert (path / dirty).read_text() == "unsaved work"


def test_drop_waits_for_lock_then_saves_work_on_branch(repo):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    (path / "file").write_text("new work\n")
    (path / "Temp").mkdir()
    lock = path / "Temp" / "UnityLockfile"
    lock.touch()
    with pytest.raises(worktree.GitError, match="Unity lockfile"):
        branch_mode.drop(checkout, "task-1")
    assert git("branch", "--show-current") == checkout.branch
    assert (path / "file").read_text() == "new work\n"
    lock.unlink()
    branch_mode.drop(checkout, "task-1")
    assert git("branch", "--show-current") == "main"
    assert (path / "file").read_text() == "original\n"
    assert git("show", f"{checkout.branch}:file") == "new work"


def test_drop_does_not_save_or_switch_when_head_moved(repo):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    git("switch", "-q", "main")
    (path / "file").write_text("outside task\n")
    with pytest.raises(worktree.GitError, match="HEAD is not on the task branch"):
        branch_mode.drop(checkout, "task-1")
    assert git("branch", "--show-current") == "main"
    assert git("status", "--porcelain") == "M file"
    assert git("rev-parse", checkout.branch) == checkout.base


def test_merge_lists_saved_files_and_fast_forwards_without_changing_them(repo):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    (path / "file").write_text("saved by Vir\n")
    (path / "new").write_text("new\n")
    assert branch_mode.changed_files(checkout) == ["file", "new"]
    commit, tip = branch_mode.merge(checkout, "Update game")
    assert git("branch", "--show-current") == "main"
    assert git("rev-parse", "HEAD") == commit
    assert git("rev-parse", "HEAD^") == checkout.base
    assert git("rev-parse", f"{tip}^") == checkout.base
    assert (path / "file").read_text() == "saved by Vir\n"
    assert (path / "new").read_text() == "new\n"
    assert not git("status", "--porcelain")


def test_merge_refuses_moved_base_without_changing_checkout(repo):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    (path / "file").write_text("pending\n")
    git("update-ref", "refs/heads/main", git("-c", "user.name=t", "-c", "user.email=t@t", "commit-tree",
                                               checkout.base + "^{tree}", "-p", checkout.base, "-m", "other"),
        checkout.base)
    before = git("status", "--porcelain")
    with pytest.raises(worktree.GitError, match="moved"):
        branch_mode.merge(checkout, "Update game")
    assert git("branch", "--show-current") == checkout.branch
    assert git("rev-parse", checkout.branch) == checkout.base
    assert git("status", "--porcelain") == before


def test_merge_rolls_back_if_index_cannot_be_installed(repo, monkeypatch):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    (path / "file").write_text("pending\n")
    before = git("status", "--porcelain")

    def fail(*args):
        raise OSError("index unavailable")

    monkeypatch.setattr(branch_mode.os, "replace", fail)
    with pytest.raises(worktree.GitError, match="rolled back"):
        branch_mode.merge(checkout, "Update game")
    assert git("branch", "--show-current") == checkout.branch
    assert git("rev-parse", "main") == checkout.base
    assert git("rev-parse", checkout.branch) == checkout.base
    assert git("status", "--porcelain") == before


def test_merge_refuses_an_existing_git_index_lock(repo):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    (path / "file").write_text("pending\n")
    lock = path / ".git" / "index.lock"
    lock.write_text("another process")
    with pytest.raises(worktree.GitError, match="cannot lock"):
        branch_mode.merge(checkout, "Update game")
    assert lock.read_text() == "another process"
    assert git("rev-parse", "main") == checkout.base
    assert git("branch", "--show-current") == checkout.branch


@pytest.mark.parametrize("content", [b"DIRC\0\0\0\2", b""])
def test_merge_keeps_and_reports_a_lock_another_process_created_after_the_marker(repo, content):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    (path / "file").write_text("pending\n")
    lock = path / ".git" / "index.lock"

    def marker(commit, tip):
        return f"{time.time_ns()}:nonce"

    def foreign_lock(commit, tip):
        lock.unlink()
        lock.write_bytes(content)
        raise worktree.GitError("stop before refs")

    with pytest.raises(worktree.GitError, match="stop before refs") as caught:
        branch_mode.merge(checkout, "Update game", before_lock=marker, before_refs=foreign_lock)
    assert branch_mode.LOCK_LEFT in str(caught.value)
    assert lock.read_bytes() == content
    assert git("branch", "--show-current") == checkout.branch
    assert git("rev-parse", "main") == checkout.base
    assert not list((path / ".git").glob("crewbots-*"))


def test_merge_leaves_no_temporary_files_or_lock(repo):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    (path / "file").write_text("pending\n")
    branch_mode.merge(checkout, "Update game")
    assert not list((path / ".git").glob("crewbots-*")) and not (path / ".git" / "index.lock").exists()
    assert not git("status", "--porcelain")


def test_clear_interrupted_lock_needs_our_prefix_and_nonce(tmp_path):
    lock = tmp_path / "index.lock"
    assert branch_mode.clear_interrupted_lock(lock, None)
    for foreign in (b"", b"DIRC\0\0", b"someone else\n", b"crewbots-lock other\n"):
        lock.write_bytes(foreign)
        assert not branch_mode.clear_interrupted_lock(lock, "n1")
        assert lock.exists()
    lock.write_bytes(b"DIRC")
    assert not branch_mode.clear_interrupted_lock(lock, None)
    lock.write_text("crewbots-lock n1\n")
    assert branch_mode.clear_interrupted_lock(lock, "n1") and not lock.exists()
    lock.write_text("crewbots-lock n1\n")  # the crash came before the marker was saved
    assert branch_mode.clear_interrupted_lock(lock, None) and not lock.exists()


def test_sweep_removes_only_files_carrying_the_recorded_nonce(repo, caplog):
    path, git = repo
    gitdir = path / ".git"
    ours = [gitdir / f"{kind}{branch_mode.temp_prefix('', 'n1:ab')}x" for kind in (branch_mode.LOCK_TEMP, branch_mode.INDEX_TEMP)]
    theirs = [gitdir / "crewbots-lock-tmp-n2-ab-x", gitdir / "crewbots-index-new-other", gitdir / "unrelated"]
    for f in [*ours, *theirs]:
        f.write_text("x")
    branch_mode.sweep_temp_files(path, None)
    assert all(f.exists() for f in [*ours, *theirs]) and "no nonce recorded" in caplog.text
    branch_mode.sweep_temp_files(path, "n1:ab")
    assert not any(f.exists() for f in ours) and all(f.exists() for f in theirs)


def test_failure_to_remove_the_temporary_lock_file_does_not_stop_the_merge(repo, monkeypatch):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    (path / "file").write_text("pending\n")
    original = branch_mode.Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name.startswith(branch_mode.LOCK_TEMP):
            raise PermissionError("no")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(branch_mode.Path, "unlink", unlink)
    marker = "123:abc"
    branch_mode.merge(checkout, "Update game", before_lock=lambda commit, tip: marker)
    monkeypatch.setattr(branch_mode.Path, "unlink", original)
    assert not (path / ".git" / "index.lock").exists()
    leftover = list((path / ".git").glob(branch_mode.LOCK_TEMP + "*"))
    assert len(leftover) == 1
    branch_mode.sweep_temp_files(path, marker)
    assert not list((path / ".git").glob("crewbots-*"))


async def prepared_merge(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("snapshot\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    return task


async def test_any_error_after_the_lock_is_taken_releases_it_and_restores_the_proposal(game, monkeypatch):
    crew, store, path, git, gate = game
    task = await prepared_merge(game, monkeypatch)
    original = branch_mode.tempfile.mkstemp

    def mkstemp(*args, **kwargs):
        if str(kwargs.get("prefix", "")).startswith(branch_mode.INDEX_TEMP):
            raise OSError("disk full")
        return original(*args, **kwargs)

    monkeypatch.setattr(branch_mode.tempfile, "mkstemp", mkstemp)
    with pytest.raises(MergeError, match="disk full"):
        crew.runner.merge(task.id)
    after = store.get_task(task.id)
    assert after.merge_state == "proposed" and after.merge_marker is None
    assert not (path / ".git" / "index.lock").exists()
    assert git("branch", "--show-current") == task.branch and git("rev-parse", "main") == task.base
    await crew.runner.shutdown()


async def test_failed_cleanup_of_the_temporary_index_keeps_the_original_error_and_releases_the_lock(game, monkeypatch):
    crew, store, path, git, gate = game
    task = await prepared_merge(game, monkeypatch)
    original = branch_mode.Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name.startswith(branch_mode.INDEX_TEMP):
            raise PermissionError("cannot unlink")
        return original(self, *args, **kwargs)

    def fail(*args):
        raise worktree.GitError("switch failed")

    monkeypatch.setattr(branch_mode.Path, "unlink", unlink)
    monkeypatch.setattr(branch_mode, "switch_head", fail)
    with pytest.raises(MergeError, match="switch failed"):
        crew.runner.merge(task.id)
    monkeypatch.setattr(branch_mode.Path, "unlink", original)
    after = store.get_task(task.id)
    assert after.merge_state == "proposed"
    assert not (path / ".git" / "index.lock").exists()
    assert git("branch", "--show-current") == task.branch and git("rev-parse", "main") == task.base
    await crew.runner.shutdown()


async def test_marker_is_kept_while_our_lock_cannot_be_removed(game, monkeypatch):
    crew, store, path, git, gate = game
    task = await prepared_merge(game, monkeypatch)
    original = branch_mode.Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == "index.lock":
            raise PermissionError("stuck")
        return original(self, *args, **kwargs)

    def fail(*args):
        raise worktree.GitError("switch failed")

    monkeypatch.setattr(branch_mode.Path, "unlink", unlink)
    monkeypatch.setattr(branch_mode, "switch_head", fail)
    with pytest.raises(MergeError, match=branch_mode.LOCK_STUCK):
        crew.runner.merge(task.id)
    monkeypatch.setattr(branch_mode.Path, "unlink", original)
    after = store.get_task(task.id)
    assert after.merge_state == "proposed" and after.merge_marker
    lock = path / ".git" / "index.lock"
    assert lock.read_text() == f"crewbots-lock {after.merge_marker}\n"
    assert branch_mode.clear_interrupted_lock(lock, after.merge_marker) and not lock.exists()
    await crew.runner.shutdown()


def test_head_switch_requires_the_expected_old_branch(repo):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    git("switch", "-q", "main")
    with pytest.raises(worktree.GitError, match="HEAD switch failed|HEAD changed"):
        branch_mode.switch_head(path, checkout.branch, checkout.original_branch)
    assert git("branch", "--show-current") == "main"


def test_failed_head_cas_does_not_switch_a_concurrently_changed_head_back(repo, monkeypatch):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    (path / "file").write_text("pending\n")

    def moved(repo, old, new):
        worktree._git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
        raise worktree.GitError("HEAD changed concurrently")

    monkeypatch.setattr(branch_mode, "switch_head", moved)
    with pytest.raises(worktree.GitError, match="rolled back"):
        branch_mode.merge(checkout, "Update game")
    assert git("branch", "--show-current") == "main"
    assert git("rev-parse", "main") == checkout.base
    assert git("rev-parse", checkout.branch) == checkout.base
    assert (path / "file").read_text() == "pending\n"


def test_head_switch_falls_back_for_older_git(repo, monkeypatch):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    original = branch_mode.subprocess.run

    def run(args, **kwargs):
        if args[1:] == ["update-ref", "--stdin"]:
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal: unknown command: symref-update")
        return original(args, **kwargs)

    monkeypatch.setattr(branch_mode.subprocess, "run", run)
    branch_mode.switch_head(path, checkout.branch, checkout.original_branch)
    assert git("branch", "--show-current") == "main"


@pytest.fixture
def game(repo, tmp_path):
    path, git = repo
    (path / "ProjectSettings").mkdir()
    (path / "ProjectSettings" / "ProjectVersion.txt").write_text("Unity 6")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Unity")
    store = Store(tmp_path / "crew.db")
    gate = asyncio.Event()
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True),
                        bot("engineer", access=Access.WORKSPACE_WRITE), gate=gate)
    project = projects.Project("game", path, "game", tmp_path / "game.md")
    crew.runner.reload(dataclasses.replace(crew.runner.crew, projects={"game": project}))
    yield crew, store, path, git, gate
    store.close()


async def test_runner_refuses_second_task_and_recovers_open_checkout(game, caplog):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    assert store.get_task(task.id).base_branch == "main"
    with pytest.raises(NotAllowed, match=f"game already has a task running in branch mode: {task.id}"):
        crew.runner.submit("engineer", "another", workspace=str(path))
    assert git("branch", "--show-current") == f"crewbots/{task.id}"
    crew.runner._recover_branch_projects()
    gate.set()
    await wait_until(crew.settled(task.id))


async def test_runner_head_watcher_stops_turn_and_notifies_chief(game):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    await wait_until(lambda: bool(crew.backend.specs))
    git("switch", "-q", "main")
    await wait_until(lambda: store.get_task(task.id).status == Status.FAILED, timeout=5)
    assert git("branch", "--show-current") == "main"
    with pytest.raises(NotAllowed, match=task.id):
        crew.runner.submit("engineer", "another", workspace=str(path))
    chief = crew.runner.chat("chief")
    assert any("HEAD left" in e.data.get("text", "") and f"dropping {task.id} releases the project" in e.data["text"]
               for e in store.events(chief.id))
    gate.set()
    await crew.runner.shutdown()


async def test_failed_switch_does_not_hold_project(game, monkeypatch):
    crew, store, path, git, gate = game
    original = worktree._git

    def fail(cwd, *args, **kwargs):
        if args[:2] == ("switch", "-c"):
            raise worktree.GitError("switch failed")
        return original(cwd, *args, **kwargs)

    with monkeypatch.context() as change:
        change.setattr(worktree, "_git", fail)
        with pytest.raises(NotAllowed, match="switch failed"):
            crew.runner.submit("engineer", "work", workspace=str(path))
    [failed] = store.list_tasks(status=Status.FAILED)
    assert failed.worktree_path is None and failed.base_branch is None
    next_task = crew.runner.submit("engineer", "retry", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(next_task.id))
    await crew.runner.shutdown()


async def test_drop_after_head_moves_releases_hold_without_git_changes(game):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    await wait_until(lambda: bool(crew.backend.specs))
    git("switch", "-q", "main")
    await wait_until(crew.settled(task.id), timeout=5)
    (path / "file").write_text("Vir's work\n")
    before = (git("status", "--porcelain"), git("for-each-ref", "--format=%(refname) %(objectname)"),
              (path / "file").read_text())
    result = crew.runner.drop_worktree(task.id)
    assert "without switching or committing" in result["reason"]
    assert store.get_task(task.id).worktree_path is None
    assert (git("status", "--porcelain"), git("for-each-ref", "--format=%(refname) %(objectname)"),
            (path / "file").read_text()) == before
    gate.set()
    await crew.runner.shutdown()


async def test_drop_with_missing_branch_releases_hold_without_touching_checkout(game):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))
    git("update-ref", "-d", f"refs/heads/{task.branch}")
    before = ((path / ".git" / "HEAD").read_text(), (path / "file").read_text(),
              git("for-each-ref", "--format=%(refname) %(objectname)"))
    result = crew.runner.drop_worktree(task.id)
    assert "without switching or committing" in result["reason"]
    assert store.get_task(task.id).worktree_path is None
    assert ((path / ".git" / "HEAD").read_text(), (path / "file").read_text(),
            git("for-each-ref", "--format=%(refname) %(objectname)")) == before
    await crew.runner.shutdown()


def advance_main_with_file(path, git, tmp_path, name, content):
    """Commit a new version of one file onto main without touching the checkout."""
    env = dict(os.environ, GIT_INDEX_FILE=str(tmp_path / "advance-index"))

    def run(*args, **kwargs):
        return subprocess.run([paths.git(), *args], cwd=path, env=env, check=True, capture_output=True, text=True,
                              **kwargs).stdout.strip()

    run("read-tree", "main")
    blob = run("hash-object", "-w", "--stdin", input=content)
    run("update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    commit = git("-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", run("write-tree"), "-p", "main", "-m", "later")
    git("update-ref", "refs/heads/main", commit)


async def test_drop_of_merged_task_still_on_its_branch_switches_back_and_releases(game, tmp_path):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))
    store.update_task(task.id, merge_state="merged", merged_commit=task.base)
    advance_main_with_file(path, git, tmp_path, "file", "later\n")
    assert git("branch", "--show-current") == task.branch
    result = crew.runner.drop_worktree(task.id)
    assert result["reason"] == "switched back to main"
    assert git("branch", "--show-current") == "main"
    assert (path / "file").read_text() == "later\n"
    assert store.get_task(task.id).worktree_path is None
    await crew.runner.shutdown()


async def test_drop_of_merged_task_refuses_when_a_local_change_is_in_the_way(game, tmp_path):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))
    store.update_task(task.id, merge_state="merged", merged_commit=task.base)
    advance_main_with_file(path, git, tmp_path, "file", "later\n")
    (path / "file").write_text("Vir's edit\n")
    before = (git("branch", "--show-current"), git("for-each-ref", "--format=%(refname) %(objectname)"))
    with pytest.raises(NotAllowed, match="overwritten"):
        crew.runner.drop_worktree(task.id)
    assert (git("branch", "--show-current"), git("for-each-ref", "--format=%(refname) %(objectname)")) == before
    assert (path / "file").read_text() == "Vir's edit\n"
    assert store.get_task(task.id).worktree_path == str(path)
    await crew.runner.shutdown()


async def test_drop_of_merged_task_does_not_overwrite_an_ignored_local_file(game, tmp_path):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))
    store.update_task(task.id, merge_state="merged", merged_commit=task.base)
    advance_main_with_file(path, git, tmp_path, "Temp/new.txt", "tracked on main\n")
    (path / "Temp").mkdir(exist_ok=True)
    (path / "Temp" / "new.txt").write_text("my cache\n")
    with pytest.raises(NotAllowed, match="Temp/new.txt"):
        crew.runner.drop_worktree(task.id)
    assert (path / "Temp" / "new.txt").read_text() == "my cache\n"
    assert git("branch", "--show-current") == task.branch
    assert store.get_task(task.id).worktree_path == str(path)
    await crew.runner.shutdown()


async def test_drop_of_merged_task_with_head_elsewhere_releases_without_touching_checkout(game):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))
    store.update_task(task.id, merge_state="merged", merged_commit=task.base)
    git("switch", "-c", "other")
    before = (git("branch", "--show-current"), git("for-each-ref", "--format=%(refname) %(objectname)"))
    result = crew.runner.drop_worktree(task.id)
    assert "without switching or committing" in result["reason"]
    assert store.get_task(task.id).worktree_path is None
    assert (git("branch", "--show-current"), git("for-each-ref", "--format=%(refname) %(objectname)")) == before
    await crew.runner.shutdown()


async def test_withdraw_after_head_moves_releases_hold_without_git_changes(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    git("switch", "-q", "main")
    (path / "file").write_text("Vir's work\n")
    before = (git("status", "--porcelain"), git("for-each-ref", "--format=%(refname) %(objectname)"))
    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        response = client.post(f"/tasks/{task.id}/withdraw-merge", json={"reason": "withdraw"},
                               headers={"Authorization": "Bearer t"})
    assert response.status_code == 200
    assert response.json()["worktree_path"] is None and response.json()["merge_state"] is None
    assert "without switching or committing" in response.json()["notice"]
    assert (git("status", "--porcelain"), git("for-each-ref", "--format=%(refname) %(objectname)")) == before
    assert "without switching or committing" in store.last_event(task.id, ("merge_withdrawn",)).data["reason"]
    await crew.runner.shutdown()


async def test_watcher_stops_a_reset_of_the_task_branch(game):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    (path / "file").write_text("first commit\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "first")
    await wait_until(lambda: bool(crew.backend.specs))
    git("reset", "--hard", "HEAD^")
    await wait_until(lambda: store.get_task(task.id).status == Status.FAILED, timeout=5)
    assert "history was rewritten" in store.get_task(task.id).error
    gate.set()
    await crew.runner.shutdown()


async def test_watcher_allows_forward_commits(game):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    await wait_until(lambda: bool(crew.backend.specs))
    (path / "file").write_text("forward commit\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "forward")
    await asyncio.sleep(2.2)
    assert store.get_task(task.id).status == Status.RUNNING
    gate.set()
    await wait_until(crew.settled(task.id))
    assert store.get_task(task.id).status == Status.IDLE
    await crew.runner.shutdown()


async def test_end_of_turn_check_catches_head_move(game):
    crew, store, path, git, gate = game
    from crewbots.events import Done

    def move(spec, me):
        git("switch", "-q", "main")
        return [Done(final_text="done")]

    crew.script["engineer"] = move
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))
    assert store.get_task(task.id).status == Status.FAILED
    assert "HEAD left" in store.get_task(task.id).error
    assert git("branch", "--show-current") == "main"
    await crew.runner.shutdown()


async def test_hidden_turn_checks_head(game):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))

    async def stream():
        from crewbots.events import Done

        git("switch", "-q", "main")
        yield Done(final_text="done")

    with pytest.raises(NotAllowed, match="HEAD left"):
        await crew.runner._hidden(task.id, stream(), crew.runner.crew.bots["engineer"])
    assert store.get_task(task.id).status == Status.FAILED
    await crew.runner.shutdown()


async def test_proposal_includes_virs_file_and_withdrawal_waits_for_lock(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("Vir saved this\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    event = store.last_events(task.id, ("merge_proposed",), 1)[0]
    assert event.data["files"] == ["file"]
    (path / "Temp").mkdir()
    (path / "Temp" / "UnityLockfile").touch()
    with pytest.raises(NotAllowed, match="Unity lockfile"):
        crew.runner.withdraw_merge(task.id, "not now")
    assert store.get_task(task.id).merge_state == "proposed"
    assert git("branch", "--show-current") == f"crewbots/{task.id}"
    (path / "Temp" / "UnityLockfile").unlink()
    crew.runner.withdraw_merge(task.id, "not now")
    assert git("branch", "--show-current") == "main"
    assert git("show", f"crewbots/{task.id}:file") == "Vir saved this"
    next_task = crew.runner.submit("engineer", "another", workspace=str(path))
    await wait_until(crew.settled(next_task.id))
    await crew.runner.shutdown()


async def test_runner_merges_project_branch_without_changing_files(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("Vir saved this\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    merged = crew.runner.merge(task.id)
    assert merged.merged_tip and merged.merge_state == "merged"
    assert git("branch", "--show-current") == "main"
    assert (path / "file").read_text() == "Vir saved this\n"
    assert not git("status", "--porcelain")
    assert git("log", "-1", "--format=%s") == "Update game"
    await crew.runner.shutdown()


async def test_failed_step_after_refs_land_marks_merged_when_base_has_advanced(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("snapshot\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")

    def advance_then_fail(repo, old, new):
        commit = store.get_task(task.id).merged_commit
        later = git("-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", commit + "^{tree}", "-p", commit, "-m", "later")
        git("update-ref", "refs/heads/main", later, commit)
        raise worktree.GitError("switch failed")

    monkeypatch.setattr(branch_mode, "switch_head", advance_then_fail)
    with pytest.raises(MergeError, match="already reached"):
        crew.runner.merge(task.id)
    after = store.get_task(task.id)
    assert after.merge_state == "merged" and after.merged_commit
    assert git("log", "-1", "--format=%s", "main") == "later"
    assert git("branch", "--show-current") == task.branch
    assert git("rev-parse", task.branch) == after.merged_tip
    assert not (path / ".git" / "index.lock").exists()
    await crew.runner.shutdown()


async def test_save_after_snapshot_is_preserved_and_reported(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("before snapshot\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    replace = branch_mode.os.replace

    def late_save(src, dst):
        (path / "file").write_text("saved during merge\n")
        (path / "new file").write_text("also saved\n")
        replace(src, dst)

    with monkeypatch.context() as change:
        change.setattr(branch_mode.os, "replace", late_save)
        merged = crew.runner.merge(task.id)
    event = store.last_event(task.id, ("merged",))
    assert merged.merge_state == "merged"
    assert git("show", "HEAD:file") == "before snapshot"
    assert (path / "file").read_text() == "saved during merge\n"
    assert (path / "new file").read_text() == "also saved\n"
    assert event.data["leftover_files"] == ["file", "new file"]
    assert event.data["leftover_check_error"] is None
    await crew.runner.shutdown()


async def test_merge_api_returns_late_saves(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("before snapshot\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    replace = branch_mode.os.replace

    def late_save(src, dst):
        (path / "file").write_text("saved during merge\n")
        replace(src, dst)

    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        with monkeypatch.context() as change:
            change.setattr(branch_mode.os, "replace", late_save)
            response = client.post(f"/tasks/{task.id}/merge", headers={"Authorization": "Bearer t"})
    assert response.status_code == 200
    assert response.json()["leftover_files"] == ["file"]
    assert response.json()["leftover_check_error"] is None
    await crew.runner.shutdown()


async def test_merge_reports_when_late_save_check_fails(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("before snapshot\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")

    def fail(repo):
        raise OSError("cannot inspect checkout")

    monkeypatch.setattr(branch_mode, "leftover_files", fail)
    merged = crew.runner.merge(task.id)
    event = store.last_event(task.id, ("merged",))
    assert merged.merge_state == "merged" and git("branch", "--show-current") == "main"
    assert event.data["leftover_files"] is None
    assert event.data["leftover_check_error"] == "cannot inspect checkout"
    await crew.runner.shutdown()


async def test_automatic_merge_tells_the_coordinator_about_late_saves(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("before snapshot\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    store.set_auto_merge(True)
    replace = branch_mode.os.replace

    def late_save(src, dst):
        (path / "file").write_text("saved during merge\n")
        replace(src, dst)

    with monkeypatch.context() as change:
        change.setattr(branch_mode.os, "replace", late_save)
        result = await CrewTools(crew.runner, parent.id, can_delegate=True).call(
            "propose_merge", {"task_id": task.id, "summary": "Update game"})
    assert "Saved after the merge started, still uncommitted: file" in result
    assert git("show", "HEAD:file") == "before snapshot"
    await crew.runner.shutdown()


async def test_runner_refuses_merge_after_base_moves(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("pending\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    base = store.get_task(task.id).base
    new_base = git("-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", base + "^{tree}", "-p", base,
                   "-m", "other")
    git("update-ref", "refs/heads/main", new_base, base)
    before = git("status", "--porcelain")
    with pytest.raises(MergeError, match="moved"):
        crew.runner.merge(task.id)
    assert git("branch", "--show-current") == f"crewbots/{task.id}"
    assert git("rev-parse", "main") == new_base
    assert git("rev-parse", "HEAD") == base
    assert git("status", "--porcelain") == before
    await crew.runner.shutdown()


async def test_merge_refuses_when_proposal_file_list_is_stale(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("pending\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    (path / "new").write_text("saved after proposal\n")
    with pytest.raises(MergeError, match="propose the merge again"):
        crew.runner.merge(task.id)
    assert git("branch", "--show-current") == f"crewbots/{task.id}"
    assert (path / "new").read_text() == "saved after proposal\n"
    await crew.runner.shutdown()


async def test_merge_compares_files_from_its_actual_snapshot(game, monkeypatch):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("pending\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    original = branch_mode.snapshot

    def changed_before_snapshot(checkout):
        (path / "new").write_text("new file\n")
        return original(checkout)

    monkeypatch.setattr(branch_mode, "snapshot", changed_before_snapshot)
    base = git("rev-parse", "main")
    with pytest.raises(MergeError, match="propose the merge again"):
        crew.runner.merge(task.id)
    assert git("rev-parse", "main") == base
    assert git("branch", "--show-current") == f"crewbots/{task.id}"
    await crew.runner.shutdown()


async def test_merge_in_recovery_cannot_be_started_again(game):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))
    store.update_task(task.id, merge_state="merging", merged_commit="expected")
    with pytest.raises(MergeError, match="awaiting recovery"):
        crew.runner.merge(task.id, force=True)
    assert git("branch", "--show-current") == task.branch
    await crew.runner.shutdown()


@pytest.mark.parametrize("crash", ["before_refs", "after_refs", "after_head"])
async def test_crashed_merge_is_recovered_on_start(game, crash):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("snapshot\n")
    store.update_task(task.id, merge_state="proposed")
    script = '''
import os, sys
from pathlib import Path
from crewbots import branch_mode
from crewbots.store import Store
db, task_id, phase = sys.argv[1:]
store = Store(Path(db))
task = store.get_task(task_id)
checkout = branch_mode.BranchCheckout(Path(task.repo), task.branch, task.base_branch, task.base)
def mark(commit, tip):
    store.update_task(task_id, merge_state="merging", merged_commit=commit, merged_tip=tip)
    if phase == "before_refs":
        os._exit(72)
def stop(*args):
    os._exit(73)
if phase == "after_refs":
    branch_mode.switch_head = stop
elif phase == "after_head":
    branch_mode.os.replace = lambda *args: os._exit(74)
branch_mode.merge(checkout, "Update game", expected_files=["file"], before_refs=mark)
'''
    process = subprocess.run([sys.executable, "-c", script, str(path.parent / "crew.db"), task.id, crash], capture_output=True,
                             text=True, timeout=15)
    assert process.returncode == {"before_refs": 72, "after_refs": 73, "after_head": 74}[crash], process.stderr
    interrupted = store.get_task(task.id)
    assert interrupted.merge_state == "merging"
    assert (path / ".git" / "index.lock").exists()
    crew.runner.resume_interrupted()
    recovered = store.get_task(task.id)
    assert not (path / ".git" / "index.lock").exists()
    assert (path / "file").read_text() == "snapshot\n"
    if crash == "before_refs":
        assert recovered.merge_state == "proposed"
        assert git("branch", "--show-current") == task.branch
        assert git("rev-parse", "main") == task.base
    else:
        assert recovered.merge_state == "merged"
        assert git("branch", "--show-current") == "main"
        assert git("rev-parse", "HEAD") == interrupted.merged_commit
        event = store.last_event(task.id, ("merged",))
        assert event.data["recovered"] is True
        if crash == "after_head":
            assert git("status", "--porcelain")
            assert "left as found" in event.data["leftover_check_error"]
        else:
            assert not git("status", "--porcelain")
    await crew.runner.shutdown()


def kill_after_ref_transaction(path, task_id):
    script = '''
import os, signal, sys, time, secrets
from pathlib import Path
from crewbots import branch_mode
from crewbots.store import Store
store = Store(Path(sys.argv[1]))
task = store.get_task(sys.argv[2])
checkout = branch_mode.BranchCheckout(Path(task.repo), task.branch, task.base_branch, task.base)
def marker(commit, tip):
    nonce = f"{time.time_ns()}:{secrets.token_hex(16)}"
    store.update_task(task.id, merge_state="merging", merged_commit=commit, merged_tip=tip,
                      merge_lock=None, merge_marker=nonce)
    return nonce
def kill(*args):
    os.kill(os.getpid(), signal.SIGKILL)
branch_mode.switch_head = kill
branch_mode.merge(checkout, "Update game", expected_files=["file"], before_lock=marker)
'''
    result = subprocess.run([sys.executable, "-c", script, str(path.parent / "crew.db"), task_id],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == -signal.SIGKILL, result.stderr


@pytest.mark.parametrize("during_recovery", [False, True])
@pytest.mark.parametrize("boundary", ["marker", "lock"])
async def test_killed_around_lock_intent_recovers(game, monkeypatch, during_recovery, boundary):
    crew, store, path, git, gate = game
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("snapshot\n")
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, latest=[]))
    crew.runner.propose_merge(parent.id, task.id, "Update game")
    if during_recovery:
        kill_after_ref_transaction(path, task.id)
    script = '''
import os, signal, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path.cwd() / "tests"))
from fakes import FakeBackend, bot, make_crew
from crewbots import branch_mode
from crewbots.runner import Runner
from crewbots.store import Store
db, task_id, boundary, recovery = sys.argv[1:]
store = Store(Path(db))
runner = Runner(store, make_crew(Path(db).parent, bot("chief", can_delegate=True), bot("engineer")),
                {"fake": FakeBackend()})
runner.review_state = lambda _: SimpleNamespace(approved=True, round=1, latest=[])
original = os.link
def intercept(src, dst, *args, **kwargs):
    if str(dst).endswith("/index.lock"):
        task = store.get_task(task_id)
        assert task.merge_state == "merging" and task.merge_marker and task.merge_lock is None
        if boundary == "marker":
            os.kill(os.getpid(), signal.SIGKILL)
        original(src, dst, *args, **kwargs)
        os.kill(os.getpid(), signal.SIGKILL)
    return original(src, dst, *args, **kwargs)
branch_mode.os.link = intercept
if recovery == "yes":
    runner._recover_branch_projects()
else:
    runner.merge(task_id)
'''
    child = subprocess.run([sys.executable, "-c", script, str(path.parent / "crew.db"), task.id, boundary,
                            "yes" if during_recovery else "no"], capture_output=True, text=True, timeout=20)
    assert child.returncode == -signal.SIGKILL, child.stderr
    interrupted = store.get_task(task.id)
    assert interrupted.merge_state == "merging" and interrupted.merge_lock is None
    timestamp, nonce = interrupted.merge_marker.split(":", 1)
    assert int(timestamp) > 0 and len(nonce) == 32
    lock = path / ".git" / "index.lock"
    assert lock.exists() == (boundary == "lock")
    assert list((path / ".git").glob("crewbots-lock-tmp-*")) or lock.exists()
    crew.runner.resume_interrupted()
    # The lock was ours from its first instant, so a crash right after the link still recovers.
    assert not lock.exists() and not list((path / ".git").glob("crewbots-*"))
    recovered = store.get_task(task.id)
    assert recovered.merge_state == ("merged" if during_recovery else "proposed")
    assert git("branch", "--show-current") == ("main" if during_recovery else task.branch)
    assert (path / "file").read_text() == "snapshot\n"
    await crew.runner.shutdown()


async def test_recovery_keeps_advanced_base_and_all_refs(game):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("snapshot\n")
    kill_after_ref_transaction(path, task.id)
    expected = store.get_task(task.id).merged_commit
    advanced = git("-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", expected + "^{tree}",
                   "-p", expected, "-m", "later")
    git("update-ref", "refs/heads/main", advanced, expected)
    before = (git("for-each-ref", "--format=%(refname) %(objectname)"),
              (path / ".git" / "HEAD").read_bytes(), (path / ".git" / "index").read_bytes(),
              (path / "file").read_bytes())
    crew.runner.resume_interrupted()
    assert store.get_task(task.id).merge_state == "merged"
    assert (git("for-each-ref", "--format=%(refname) %(objectname)"),
            (path / ".git" / "HEAD").read_bytes(), (path / ".git" / "index").read_bytes(),
            (path / "file").read_bytes()) == before
    assert not (path / ".git" / "index.lock").exists()
    event = store.last_event(task.id, ("merged",))
    assert "left as found" in event.data["leftover_check_error"]
    assert advanced[:12] in event.data["leftover_check_error"] and task.branch in event.data["leftover_check_error"]
    await crew.runner.shutdown()


@pytest.mark.parametrize("expected_in_base", [False, True])
async def test_recovery_keeps_and_reports_a_lock_it_did_not_create(game, caplog, expected_in_base):
    crew, store, path, git, gate = game
    task = crew.runner.submit("engineer", "work", workspace=str(path))
    gate.set()
    await wait_until(crew.settled(task.id))
    if expected_in_base:
        (path / "file").write_text("snapshot\n")
        kill_after_ref_transaction(path, task.id)
        interrupted = store.get_task(task.id)
        expected, tip = interrupted.merged_commit, interrupted.merged_tip
    else:
        expected = git("-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", task.base + "^{tree}",
                       "-p", task.base, "-m", "not merged")
        tip = task.base
    marker_time = time.time_ns()
    lock = path / ".git" / "index.lock"
    lock.write_text("someone else's lock")
    os.utime(lock, ns=(marker_time + 10_000_000_000, marker_time + 10_000_000_000))
    store.update_task(task.id, merge_state="merging", merged_commit=expected, merged_tip=tip,
                      merge_lock=None, merge_marker=f"{marker_time}:nonce")
    crew.runner.resume_interrupted()
    assert lock.read_text() == "someone else's lock"
    assert branch_mode.LOCK_LEFT in caplog.text
    assert branch_mode.LOCK_LEFT in json.dumps([e.data for e in store.events(task.id)])
    assert store.get_task(task.id).merge_state == ("merged" if expected_in_base else "proposed")
    assert git("branch", "--show-current") == task.branch
    await crew.runner.shutdown()


async def test_killed_after_switch_before_link_is_recovered(game):
    crew, store, path, git, gate = game
    task = store.create_task("engineer", "work", workspace=str(path))
    script = '''
import os, signal, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from fakes import FakeBackend, bot, make_crew
from crewbots.projects import Project
from crewbots.runner import Runner
from crewbots.store import Store
db, task_id, folder = sys.argv[1:]
store = Store(Path(db))
runner = Runner(store, make_crew(Path(db).parent, bot("engineer")), {"fake": FakeBackend()})
original = store.update_task
def update(task_id, **fields):
    if "checkout_state" in fields and fields["checkout_state"] is None:
        os.kill(os.getpid(), signal.SIGKILL)
    return original(task_id, **fields)
store.update_task = update
runner._start_branch(store.get_task(task_id), Project("game", Path(folder), "", Path(db).parent / "game.md"))
'''
    child = subprocess.run([sys.executable, "-c", script, str(path.parent / "crew.db"), task.id, str(path)],
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == -signal.SIGKILL, child.stderr
    interrupted = store.get_task(task.id)
    assert interrupted.checkout_state == "starting"
    assert interrupted.worktree_path == str(path) and interrupted.base_branch == "main"
    assert git("branch", "--show-current") == f"crewbots/{task.id}"
    crew.runner.resume_interrupted()
    linked = store.get_task(task.id)
    assert linked.checkout_state is None and linked.worktree_path == str(path)
    assert git("branch", "--show-current") == f"crewbots/{task.id}"
    await crew.runner.shutdown()


async def test_starting_marker_without_switch_clears_hold_without_changes(game):
    crew, store, path, git, gate = game
    task = store.create_task("engineer", "work", workspace=str(path))
    store.update_task(task.id, repo=str(path), worktree_path=str(path), branch=f"crewbots/{task.id}",
                      base=git("rev-parse", "HEAD"), base_branch="main", checkout_state="starting")
    before = (git("for-each-ref", "--format=%(refname) %(objectname)"), git("status", "--porcelain"))
    crew.runner.resume_interrupted()
    cleared = store.get_task(task.id)
    assert cleared.checkout_state is None and cleared.worktree_path is None and cleared.base_branch is None
    assert git("branch", "--show-current") == "main"
    assert (git("for-each-ref", "--format=%(refname) %(objectname)"), git("status", "--porcelain")) == before
    await crew.runner.shutdown()


async def test_review_reads_the_project_checkout(game):
    crew, store, path, git, gate = game
    reviewer = bot("reviewer", access=Access.WORKSPACE_WRITE)
    crew.runner.reload(dataclasses.replace(crew.runner.crew, bots=crew.runner.crew.bots | {"reviewer": reviewer}))
    parent = store.create_task("chief", "work")
    task = crew.runner.submit("engineer", "work", workspace=str(path), parent_id=parent.id)
    gate.set()
    await wait_until(crew.settled(task.id))
    (path / "file").write_text("Vir saved this\n")
    review = crew.runner.delegate(parent.id, "reviewer", "review", review_of=task.id)
    await wait_until(crew.settled(review.id))
    spec = crew.specs_for("reviewer")[-1]
    assert spec.cwd == path and spec.access == Access.READ_ONLY
    assert "Vir saved this" in crew.runner.changes(task.id)
    await crew.runner.shutdown()


async def test_startup_leaves_unknown_task_branch_untouched(game, caplog):
    crew, store, path, git, gate = game
    git("switch", "-qc", "crewbots/missing")
    crew.runner._recover_branch_projects()
    assert "left unchanged" in caplog.text
    assert git("branch", "--show-current") == "crewbots/missing"


def test_seeded_snapshot_matches_unseeded_one(repo, monkeypatch):
    path, git = repo
    for name in ("keep", "edit", "gone", "touched"):
        (path / name).write_text(f"{name}\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "more files")
    git("status")  # refresh the real index's stat cache
    checkout = branch_mode.start(path, "task-1")
    (path / "edit").write_text("changed, longer\n")
    (path / "gone").unlink()
    (path / "touched").write_text("touched\n")
    (path / "added").write_text("new\n")
    (path / "dir").mkdir()
    (path / "dir" / "untracked").write_text("x\n")

    def listing(seeded):
        monkeypatch.setattr(branch_mode, "seed_index", branch_mode.seed_index if seeded else lambda *_: False)
        with branch_mode.snapshot(checkout) as (g, _):
            return g("ls-files", "-s", "-z"), g("write-tree")

    real = (path / ".git" / "index").read_bytes()
    seeded, unseeded = listing(True), listing(False)
    assert seeded == unseeded
    assert {"edit", "gone", "added", "dir/untracked"} <= set(branch_mode.changed_files(checkout))
    assert (path / ".git" / "index").read_bytes() == real


def test_snapshot_is_not_seeded_from_a_sparse_or_flagged_index(repo):
    path, git = repo
    target = path / "seed"
    assert branch_mode.seed_index(path, target)
    target.unlink()
    git("update-index", "--assume-unchanged", "file")
    assert not branch_mode.seed_index(path, target) and not target.exists()
    git("update-index", "--no-assume-unchanged", "file")
    git("update-index", "--skip-worktree", "file")
    assert not branch_mode.seed_index(path, target)
    git("update-index", "--no-skip-worktree", "file")
    git("config", "core.splitIndex", "true")
    assert not branch_mode.seed_index(path, target)
    git("config", "core.splitIndex", "false")
    git("config", "core.sparseCheckout", "true")
    assert not branch_mode.seed_index(path, target)


def commit(git, message="c"):
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)


def trees(path, checkout, monkeypatch):
    """write-tree of the snapshot with and without seeding, and whether the real index stayed as it was."""
    real = (path / ".git" / "index").read_bytes()
    seeded_calls = []
    seed = branch_mode.seed_index

    def counting(*args):
        seeded_calls.append(seed(*args))
        return seeded_calls[-1]

    result = {}
    for name, fn in (("seeded", counting), ("unseeded", lambda *_: False)):
        monkeypatch.setattr(branch_mode, "seed_index", fn)
        with branch_mode.snapshot(checkout) as (g, _):
            result[name] = g("write-tree").strip()
    assert (path / ".git" / "index").read_bytes() == real
    return result, seeded_calls


def test_seeded_snapshot_ignores_staged_entries_that_head_lacks(repo, monkeypatch):
    path, git = repo
    (path / ".gitignore").write_text("Temp/\n*.log\n")
    (path / "forced.log").write_text("forced\n")
    git("add", "-f", "forced.log")
    checkout = branch_mode.BranchCheckout(path, "main", "main", git("rev-parse", "HEAD"))
    out, calls = trees(path, checkout, monkeypatch)
    assert calls == [True] and out["seeded"] == out["unseeded"]


def test_seeded_snapshot_drops_a_staged_unity_lockfile(repo, monkeypatch):
    path, git = repo
    (path / ".gitignore").write_text("")
    git("add", ".gitignore")
    commit(git)
    (path / "Temp").mkdir()
    (path / "Temp" / "UnityLockfile").write_text("locked")
    git("add", "Temp/UnityLockfile")
    checkout = branch_mode.BranchCheckout(path, "main", "main", git("rev-parse", "HEAD"))
    out, calls = trees(path, checkout, monkeypatch)
    assert calls == [True] and out["seeded"] == out["unseeded"]
    assert "UnityLockfile" not in git("ls-tree", "-r", "--name-only", out["seeded"])


def test_seeded_snapshot_keeps_a_head_file_staged_for_removal_and_now_ignored(repo, monkeypatch):
    path, git = repo
    git("rm", "--cached", "-q", "file")
    (path / ".gitignore").write_text("Temp/\nfile\n")
    checkout = branch_mode.BranchCheckout(path, "main", "main", git("rev-parse", "HEAD"))
    out, calls = trees(path, checkout, monkeypatch)
    assert calls == [True] and out["seeded"] == out["unseeded"]
    assert "file" in git("ls-tree", "--name-only", out["seeded"]).split()


def test_seeded_snapshot_sees_a_racily_clean_edit(repo, monkeypatch):
    path, git = repo
    git("config", "core.trustctime", "false")
    (path / "racy").write_text("aaaa\n")
    git("add", "racy")
    commit(git)
    checkout = branch_mode.BranchCheckout(path, "main", "main", git("rev-parse", "HEAD"))
    index = path / ".git" / "index"
    stamp = (path / "racy").stat().st_mtime_ns
    os.utime(index, ns=(stamp, stamp))  # the index is as old as the entry: git cannot trust the entry's stat data
    (path / "racy").write_text("bbbb\n")
    os.utime(path / "racy", ns=(stamp, stamp))
    out, calls = trees(path, checkout, monkeypatch)
    assert calls == [True] and out["seeded"] == out["unseeded"]
    assert git("show", f"{out['seeded']}:racy") == "bbbb"


def test_failed_config_query_falls_back_to_the_unseeded_snapshot(repo, monkeypatch):
    path, git = repo
    git("config", "core.splitIndex", "maybe")
    checkout = branch_mode.BranchCheckout(path, "main", "main", git("rev-parse", "HEAD"))
    (path / "new").write_text("x\n")
    out, calls = trees(path, checkout, monkeypatch)
    assert calls == [False] and out["seeded"] == out["unseeded"]


def test_failure_after_seeding_retries_unseeded(repo, monkeypatch):
    path, git = repo
    checkout = branch_mode.BranchCheckout(path, "main", "main", git("rev-parse", "HEAD"))
    (path / "new").write_text("x\n")
    with branch_mode.snapshot(checkout) as (g, _):
        expected = g("write-tree").strip()
    seeds = []

    def broken(repo_, target):
        seeds.append(target)
        target.write_bytes(b"not an index")
        return True

    monkeypatch.setattr(branch_mode, "seed_index", broken)
    with branch_mode.snapshot(checkout) as (g, _):
        assert g("write-tree").strip() == expected
    assert len(seeds) == 1


def ignored_temp_project(path):
    (path / "Temp").mkdir()
    (path / "Temp" / "UnityLockfile").write_text("locked")
    (path / "Temp" / "cache").write_text("cache")


def test_ignored_temp_folder_with_lockfile_does_not_break_the_snapshot_or_merge(repo):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    ignored_temp_project(path)
    (path / "file").write_text("edited\n")
    (path / "new").write_text("new\n")
    assert branch_mode.changed_files(checkout) == ["file", "new"]
    with branch_mode.snapshot(checkout) as (g, _):
        assert "Temp" not in g("ls-files", "-z").replace("\0", "\n")
    commit, tip = branch_mode.merge(checkout, "Update game")
    assert "UnityLockfile" not in git("ls-tree", "-r", "--name-only", commit)
    assert "UnityLockfile" not in git("ls-tree", "-r", "--name-only", tip)
    assert (path / "Temp" / "UnityLockfile").read_text() == "locked"


def test_ignored_temp_folder_does_not_break_withdrawal(repo):
    path, git = repo
    checkout = branch_mode.start(path, "task-1")
    ignored_temp_project(path)
    (path / "file").write_text("edited\n")
    (path / "Temp" / "UnityLockfile").unlink()  # the editor has closed; drop refuses while the lockfile exists
    branch_mode.drop(checkout, "task-1")
    assert git("show", f"{checkout.branch}:file") == "edited"
    assert "Temp" not in git("ls-tree", "-r", "--name-only", checkout.branch)


@pytest.mark.parametrize("seeded", [True, False])
def test_a_tracked_unity_lockfile_keeps_heads_entry_in_the_snapshot(repo, monkeypatch, seeded):
    path, git = repo
    (path / ".gitignore").write_text("")
    (path / "Temp").mkdir()
    (path / "Temp" / "UnityLockfile").write_text("in head")
    git("add", "-A")
    commit(git, "tracks the lockfile")
    checkout = branch_mode.BranchCheckout(path, "main", "main", git("rev-parse", "HEAD"))
    (path / "Temp" / "UnityLockfile").write_text("changed by the editor")
    if not seeded:
        monkeypatch.setattr(branch_mode, "seed_index", lambda *_: False)
    with branch_mode.snapshot(checkout) as (g, _):
        tree = g("write-tree").strip()
    assert git("show", f"{tree}:Temp/UnityLockfile") == "in head"
