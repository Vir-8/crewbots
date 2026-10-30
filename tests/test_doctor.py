"""`crewbots doctor`: what it finds, on a crew made for the test (the test's own home folder, from conftest)."""

import pytest

from crewbots import doctor, paths
from crewbots.store import Status, Store


@pytest.fixture
def signed_in(monkeypatch):
    answers = {("claude", "--version"): "2.1.310 (Claude Code)",
               ("claude", "auth", "status"): '{"loggedIn": true, "authMethod": "claude.ai", "email": "me@x.invalid"}',
               ("codex", "--version"): "codex-cli 0.164.0", ("codex", "login", "status"): "Logged in using ChatGPT"}
    monkeypatch.setattr(doctor, "_cli", lambda *argv: answers.get(argv))
    monkeypatch.setattr(doctor.svc, "status", lambda: doctor.svc.ServiceStatus("x", False, False, None, paths.home()))


def lines(findings):
    return [f.line for f in findings]


def test_a_healthy_crew_reads_as_healthy(tmp_path, signed_in):
    home = tmp_path / "crew"
    home.mkdir()
    store = Store(home / "crewbots.db")
    store.create_task("worker", "hello")
    store.close()

    found = doctor.run(home)

    assert "Claude Code 2.1.310: signed in (claude.ai)" in lines(found)
    assert not any("me@x.invalid" in line for line in lines(found))  # never the account's email
    assert "Codex 0.164.0: signed in using ChatGPT" in lines(found)
    assert any(f.ok and f.line.startswith("Database:") and "intact; 1 tasks" in f.line for f in found)
    assert any(f.ok is False and f.line == "Daemon: not running" for f in found)
    assert any(f.ok is None and "Login service: not installed" in f.line for f in found)


def test_signed_out_clis_say_how_to_sign_in(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "_cli", lambda *argv: {"--version": "1.0 (x)"}.get(argv[-1], "Not logged in"))

    found = doctor._claude() + doctor._codex()

    assert [(f.ok, f.hint) for f in found] == [(False, "claude auth login"), (False, "codex login")]


def test_a_damaged_database_is_reported(tmp_path, signed_in):
    home = tmp_path / "crew"
    home.mkdir()
    (home / "crewbots.db").write_bytes(b"not a database at all" * 100)

    [db] = doctor._database(home)

    assert db.ok is False and "back up" in db.hint


def test_missing_chief_is_reported(tmp_path):
    home = tmp_path / "crew"
    home.mkdir()
    store = Store(home / "crewbots.db")
    store.set_chief("gone")
    store.close()
    [finding] = doctor._chief(home)
    assert finding.ok is False and "gone" in finding.line and "disabled" in finding.line


def test_worktrees_no_task_needs_are_listed_with_how_to_remove_them(tmp_path, signed_in):
    home = tmp_path / "crew"
    home.mkdir()
    store = Store(home / "crewbots.db")
    live = store.create_task("worker", "still going")
    done = store.create_task("worker", "merged")
    for task in (live, done):
        store.update_task(task.id, worktree_path=str(paths.worktrees() / "app-1" / task.id), repo=str(tmp_path / "app"))
    store.update_task(done.id, merge_state="merged", status=Status.IDLE)
    store.close()
    for name in (live.id, done.id, "t_gone000000"):
        (paths.worktrees() / "app-1" / name).mkdir(parents=True)
    (paths.worktrees() / "empty-2").mkdir()

    [found] = doctor._worktrees(home)

    assert found.line == "Worktrees: 3 left over that no task needs"
    assert f"worktree remove {paths.worktrees() / 'app-1' / done.id}" in found.hint
    assert f"rm -r {paths.worktrees() / 'app-1' / 't_gone000000'}" in found.hint
    assert f"rmdir {paths.worktrees() / 'empty-2'}" in found.hint
    assert live.id not in found.hint
