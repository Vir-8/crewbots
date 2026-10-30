import subprocess

import pytest
from fakes import git_repo

from crewbots import worktree


@pytest.fixture
def repo(tmp_path):
    return git_repo(tmp_path / "repo")


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


def test_prune_failure_is_logged_after_checkout_and_branch_are_removed(repo, tmp_path, monkeypatch, caplog):
    wt = worktree.create(repo, "task-5", root=tmp_path / "wts")
    original = worktree._git

    def fail(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "prune"):
            raise worktree.GitError("prune failed")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", fail)
    assert worktree.remove(wt, delete_branch=True)
    assert not wt.path.exists()
    assert "prune failed" in caplog.text
    with pytest.raises(worktree.GitError):
        original(repo, "rev-parse", "--verify", wt.branch)


def test_names_that_could_escape_the_root_are_refused(tmp_path):
    with pytest.raises(ValueError):
        worktree.create(tmp_path, "../outside", root=tmp_path / "wts")


def test_git_failure_reports_both_output_streams(tmp_path, monkeypatch):
    result = subprocess.CompletedProcess([], 1, stdout=" stdout detail \n", stderr=" stderr detail \n")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)

    with pytest.raises(worktree.GitError, match="stdout detail\\nstderr detail"):
        worktree._git(tmp_path, "merge", "branch")


@pytest.mark.parametrize(("returncode", "status"), [(1, "exit code 1"), (-9, "terminated by signal 9")])
def test_git_failure_without_output_reports_status(tmp_path, monkeypatch, returncode, status):
    result = subprocess.CompletedProcess([], returncode, stdout="  \n", stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)

    with pytest.raises(worktree.GitError, match=status):
        worktree._git(tmp_path, "merge", "branch")


def test_already_in_raises_for_a_commit_that_isnt_there(repo):
    head = worktree._git(repo, "rev-parse", "HEAD")

    assert worktree.already_in(repo, head, head, head)
    with pytest.raises(worktree.GitError) as raised:
        worktree.already_in(repo, head, "0" * 40, head)
    assert raised.value.code == 128
