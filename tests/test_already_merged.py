"""A proposal whose changes already reached the target branch: nothing to merge, and the folder is left alone."""

import shutil
import tempfile
from pathlib import Path

import pytest
from fakes import ScriptedCrew, bot, git_repo
from fastapi.testclient import TestClient

from crewbots import worktree
from crewbots.api import create_app
from crewbots.runner.common import MergeError
from crewbots.store import Status, Store

HEADERS = {"Authorization": "Bearer t"}


def commit(path, name, text="x"):
    (path / name).write_text(text)
    worktree._git(path, "add", name)
    worktree._git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", f"add {name}")


@pytest.fixture
def old_proposal(tmp_path):
    """A worktree-mode proposal as made before branch mode: no base_branch, and its checkout already removed."""
    repo = git_repo(tmp_path / "repo")
    store = Store(tmp_path / "crew.db")
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    task = store.create_task("engineer", "work", status=Status.IDLE)
    wt = worktree.create(repo, task.id, root=tmp_path / "wts")
    store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base,
                      merge_state="proposed")
    commit(wt.path, "feature.txt", "feature")
    shutil.rmtree(wt.path)
    worktree._git(repo, "worktree", "prune")
    yield crew, store, repo, task, wt
    store.close()


def approve(crew, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(crew.runner, "review_state", lambda _: SimpleNamespace(approved=True, round=1, rounds=[1],
                                                                                version=1, latest=[]))


def test_branch_already_merged_into_main_is_not_merged_again(old_proposal, monkeypatch):
    crew, store, repo, task, wt = old_proposal
    worktree._git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "--no-ff", "-qm", "merged elsewhere", wt.branch)
    (repo / "README.md").write_text("Vir's own edit")  # a dirty folder must not hide it
    approve(crew, monkeypatch)
    head = worktree._git(repo, "rev-parse", "HEAD")
    assert crew.runner.already_on(store.get_task(task.id)) == "main"
    with pytest.raises(MergeError, match="already on main"):
        crew.runner.merge(task.id)
    assert worktree._git(repo, "rev-parse", "HEAD") == head
    assert (repo / "README.md").read_text() == "Vir's own edit"
    assert not wt.path.exists()  # nothing was restored
    assert store.get_task(task.id).merge_state == "proposed"


def test_same_changes_made_another_way_count_as_merged(old_proposal, monkeypatch):
    crew, store, repo, task, wt = old_proposal
    commit(repo, "feature.txt", "feature")  # same content, a different commit
    approve(crew, monkeypatch)
    assert crew.runner.already_on(store.get_task(task.id)) == "main"
    with pytest.raises(MergeError, match="already on main"):
        crew.runner.merge(task.id, force=True)


def test_work_not_on_main_still_reaches_the_folder_check(old_proposal, monkeypatch):
    crew, store, repo, task, wt = old_proposal
    (repo / "README.md").write_text("Vir's own edit")
    approve(crew, monkeypatch)
    assert crew.runner.already_on(store.get_task(task.id)) is None
    with pytest.raises(MergeError, match="uncommitted changes"):
        crew.runner.merge(task.id)


def test_conflicting_change_is_not_called_merged(old_proposal):
    crew, store, repo, task, wt = old_proposal
    commit(repo, "feature.txt", "something else")
    assert crew.runner.already_on(store.get_task(task.id)) is None


def test_status_route_and_withdraw_on_an_old_proposal(old_proposal):
    crew, store, repo, task, wt = old_proposal
    worktree._git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "merge", "--no-ff", "-qm", "merged elsewhere", wt.branch)
    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        assert client.get(f"/tasks/{task.id}/merge-status", headers=HEADERS).json() == {"already_on": "main"}
        response = client.post(f"/tasks/{task.id}/withdraw-merge", json={"reason": "already on main"}, headers=HEADERS)
        assert response.status_code == 200 and response.json()["merge_state"] is None
        assert client.get(f"/tasks/{task.id}/merge-status", headers=HEADERS).json() == {"already_on": None}


def repo_state(repo):
    """Everything the status check must leave alone: object files, index bytes, refs and the working files."""
    git_dir = repo / ".git"
    objects = sorted(str(p.relative_to(git_dir)) for p in (git_dir / "objects").rglob("*") if p.is_file())
    files = {str(p.relative_to(repo)): p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}
    return (objects, (git_dir / "index").read_bytes(), worktree._git(repo, "for-each-ref"), files)


@pytest.mark.parametrize("main_change", ["other.txt", "feature.txt"])
def test_status_check_writes_nothing_to_the_repository(old_proposal, main_change):
    """Diverged histories: once merging cleanly (main's other.txt) and once conflicting (main's feature.txt)."""
    crew, store, repo, task, wt = old_proposal
    commit(repo, main_change, "main's own")
    (repo / "README.md").write_text("Vir's edit, not staged")  # a stale index entry git status would refresh
    before = repo_state(repo)
    assert crew.runner.already_on(store.get_task(task.id)) is None
    assert repo_state(repo) == before
    assert list(Path(tempfile.gettempdir()).glob("crewbots-merge-tree-*")) == []


def test_partial_overlap_is_not_already_on_main(old_proposal):
    crew, store, repo, task, wt = old_proposal
    # The task adds feature.txt and a second file; main has only the first of the two changes.
    task_row = store.get_task(task.id)
    worktree._git(repo, "worktree", "add", "-q", str(wt.path), wt.branch)
    commit(wt.path, "second.txt", "second")
    worktree._git(repo, "worktree", "remove", "--force", str(wt.path))
    commit(repo, "feature.txt", "feature")
    assert crew.runner.already_on(task_row) is None
    commit(repo, "second.txt", "second")
    assert crew.runner.already_on(task_row) == "main"


@pytest.mark.parametrize("base", ["0" * 40, "not-a-commit"])
def test_missing_or_wrong_merge_base_is_not_already_on_main(old_proposal, base):
    crew, store, repo, task, wt = old_proposal
    commit(repo, "other.txt", "main's own")
    store.update_task(task.id, base=base)
    before = repo_state(repo)
    assert crew.runner.already_on(store.get_task(task.id)) is None
    assert repo_state(repo) == before
