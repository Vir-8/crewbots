import json

import pytest
from fakes import FakeBackend, as_bot, bot, git_repo, make_crew, start
from test_worktree_cleanup import setup_task

from crewbots import worktree
from crewbots.crew_tools import CrewTools
from crewbots.runner import Runner
from crewbots.store import Status, Store


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("chief", can_delegate=True), bot("worker")), {"fake": FakeBackend()})
    yield runner, store, tmp_path
    store.close()


def test_bot_route_refuses_a_non_coordinator(crew_app):
    client, backend, _ = crew_app
    _, chief_token = start(client, backend, "chief")
    _, worker_token = start(client, backend, "engineer")
    assert client.post("/bot/tools/sweep_worktrees", json={}, headers=as_bot(worker_token)).status_code == 422
    assert client.post("/bot/tools/sweep_worktrees", json={}, headers=as_bot(chief_token)).status_code == 200


async def test_dry_run_changes_nothing_and_a_real_run_removes_only_what_is_safe(setup):
    runner, store, tmp_path = setup
    repo = git_repo(tmp_path / "repo")
    merged, merged_wt = setup_task(store, repo, tmp_path / "wts", merge_state="merged")
    store.update_task(merged.id, merged_commit=worktree._git(repo, "rev-parse", "HEAD"))
    open_task, open_wt = setup_task(store, repo, tmp_path / "wts", Status.RUNNING)
    unmerged, unmerged_wt = setup_task(store, repo, tmp_path / "wts")
    (unmerged_wt.path / "work.txt").write_text("unmerged")
    worktree.commit(unmerged_wt, "unmerged work")
    chief = CrewTools(runner, store.create_task("chief", "x").id, can_delegate=True)

    dry = json.loads(await chief.call("sweep_worktrees", {}))
    assert {e["task_id"] for e in dry["would_remove"]} == {merged.id, unmerged.id}
    assert [e["task_id"] for e in dry["kept"]] == [open_task.id] and dry["kept"][0]["reason"]
    assert "1 unmerged commit(s)" in next(e for e in dry["would_remove"] if e["task_id"] == unmerged.id)["reason"]
    assert next(e for e in dry["would_remove"] if e["task_id"] == merged.id)["reason"] == "merged and clean"
    assert all(wt.path.is_dir() for wt in (merged_wt, open_wt, unmerged_wt))

    real = json.loads(await chief.call("sweep_worktrees", {"dry_run": False}))
    assert {e["task_id"] for e in real["removed"]} == {merged.id, unmerged.id}
    assert not merged_wt.path.exists() and open_wt.path.is_dir()
    assert worktree._git(repo, "show", f"{unmerged_wt.branch}:work.txt") == "unmerged"
