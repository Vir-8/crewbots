import subprocess

import pytest

from crewbots import worktree


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git = lambda *a: subprocess.run(["git", *a], cwd=path, check=True, capture_output=True)  # noqa: E731
    git("init", "-q", "-b", "main")
    (path / "README.md").write_text("hello\n")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return path


def test_a_worktree_is_its_own_checkout_on_its_own_branch(repo, tmp_path):
    wt = worktree.create(repo, "task-1", root=tmp_path / "wts")

    assert wt.path.is_dir() and wt.path.is_relative_to(tmp_path / "wts")
    assert wt.branch == "crewbots/task-1"
    (wt.path / "README.md").write_text("changed\n")
    assert (repo / "README.md").read_text() == "hello\n"


def test_changes_include_new_files_and_commits(repo, tmp_path):
    wt = worktree.create(repo, "task-2", root=tmp_path / "wts")
    (wt.path / "new.py").write_text("print('hi')\n")
    assert worktree.commit(wt, "add new.py")
    (wt.path / "README.md").write_text("edited\n")

    diff = worktree.changes(wt)

    assert "new.py" in diff and "+edited" in diff


def test_commit_with_nothing_to_commit_returns_none(repo, tmp_path):
    wt = worktree.create(repo, "task-3", root=tmp_path / "wts")

    assert worktree.commit(wt, "nothing") is None


def test_remove_deletes_the_checkout_and_optionally_the_branch(repo, tmp_path):
    wt = worktree.create(repo, "task-4", root=tmp_path / "wts")

    worktree.remove(wt, delete_branch=True)

    assert not wt.path.exists()
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "crewbots/task-4" not in branches


def test_names_that_could_escape_the_root_are_refused(repo, tmp_path):
    with pytest.raises(ValueError):
        worktree.create(repo, "../outside", root=tmp_path / "wts")
