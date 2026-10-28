"""Dropping work on purpose: a coordinator's `drop_task`, and a worker's "dropped:" handoff."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until

from crewbots import worktree
from crewbots.backends import Access
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done
from crewbots.runner.common import NotAllowed
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def crew_of(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("eng-lead", can_delegate=True),
                        bot("engineer", access=Access.WORKSPACE_WRITE))
    store.set_stall_minutes(15)
    return crew


def finished_work(store, tmp_path, owner="eng-lead", status=Status.IDLE, merge_state=None):
    """A delegated task with a real worktree, handed back."""
    repo = tmp_path / "repo" if (tmp_path / "repo").exists() else git_repo(tmp_path / "repo")
    parent = store.create_task(owner, "lead", status=Status.IDLE)
    work = store.create_task("engineer", "notice tidy-up", parent_id=parent.id, created_by=owner, status=status)
    wt = worktree.create(repo, work.id, root=tmp_path / "wts")
    store.update_task(work.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base, turns=1,
                      merge_state=merge_state)
    store.append_event(work.id, kind="handoff", data={})
    return parent, store.get_task(work.id), wt, repo


def later(minutes):
    return datetime.now(UTC) + timedelta(minutes=minutes)


async def test_a_dropped_task_is_closed_its_folder_removed_and_its_branch_kept(store, tmp_path):
    crew = crew_of(store, tmp_path)
    parent, work, wt, repo = finished_work(store, tmp_path)
    (wt.path / "draft.txt").write_text("not committed")
    assert [t.id for t in store.owed_by_delegator("eng-lead")] == [work.id]

    tools = CrewTools(crew.runner, parent.id, can_delegate=True)
    reply = await tools.call("drop_task", {"task_id": work.id, "reason": "a duplicate of t_123, already merged"})

    assert reply.startswith(f"Dropped {work.id}")
    task = store.get_task(work.id)
    assert task.merge_state == "dropped"
    [event] = [e for e in store.events(work.id) if e.kind == "dropped"]
    assert event.data == {"by": "eng-lead", "reason": "a duplicate of t_123, already merged"}
    assert not wt.path.exists()
    assert worktree._git(repo, "show", f"{wt.branch}:draft.txt") == "not committed"  # saved on its branch
    assert store.owed_by_delegator("eng-lead") == []
    assert crew.runner.stall_check(later(60)) == []
    assert await tools.call("drop_task", {"task_id": work.id, "reason": "again"}) == f"{work.id} was already dropped."
    await crew.runner.shutdown()


async def test_only_the_bot_that_handed_it_out_or_the_chief_drops_it(store, tmp_path):
    crew = crew_of(store, tmp_path)
    parent, work, _, _ = finished_work(store, tmp_path, owner="chief")
    lead = store.create_task("eng-lead", "lead", status=Status.IDLE)
    with pytest.raises(NotAllowed, match="wasn't handed out by you"):
        await crew.runner.drop(work.id, "not needed", by_task=lead.id)
    worker = store.create_task("engineer", "chat", kind="chat", status=Status.IDLE)
    with pytest.raises(ToolRefused, match="not one of this bot's crew tools"):
        await CrewTools(crew.runner, worker.id, can_delegate=False).call("drop_task", {"task_id": work.id, "reason": "x"})

    _, other, _, _ = finished_work(store, tmp_path, owner="eng-lead")
    chief = store.create_task("chief", "chat", kind="chat", status=Status.IDLE)
    result = await crew.runner.drop(other.id, "covered by a later merge", by_task=chief.id)
    assert result["status"] == "dropped" and store.get_task(other.id).merge_state == "dropped"
    await crew.runner.shutdown()


async def test_dropping_refuses_work_that_is_proposed_merged_running_or_without_a_reason(store, tmp_path):
    crew = crew_of(store, tmp_path)
    parent, proposed, _, _ = finished_work(store, tmp_path, merge_state="proposed")
    with pytest.raises(NotAllowed, match="withdraw the proposal first"):
        await crew.runner.drop(proposed.id, "not needed", by_task=parent.id)
    parent, merged, _, _ = finished_work(store, tmp_path, merge_state="merged")
    with pytest.raises(NotAllowed, match="nothing to drop"):
        await crew.runner.drop(merged.id, "not needed", by_task=parent.id)
    parent, work, _, _ = finished_work(store, tmp_path)
    with pytest.raises(NotAllowed, match="one-line reason"):
        await crew.runner.drop(work.id, "  ", by_task=parent.id)
    crew.runner._runs[work.id] = None  # a turn under way
    with pytest.raises(NotAllowed, match="still working"):
        await crew.runner.drop(work.id, "not needed", by_task=parent.id)
    del crew.runner._runs[work.id]
    await crew.runner.shutdown()


async def test_work_waiting_on_a_question_is_stopped_when_dropped(store, tmp_path):
    crew = crew_of(store, tmp_path)
    parent, work, wt, _ = finished_work(store, tmp_path, status=Status.NEEDS_INPUT)
    await crew.runner.drop(work.id, "the check had nothing to merge", by_task=parent.id)
    task = store.get_task(work.id)
    assert task.status is Status.CANCELLED and task.merge_state == "dropped" and not wt.path.exists()
    await crew.runner.shutdown()


async def test_a_dropped_handoff_closes_the_work_once_its_turn_ends(store, tmp_path):
    crew = crew_of(store, tmp_path)
    repo = git_repo(tmp_path / "repo")
    parent = store.create_task("eng-lead", "lead", status=Status.IDLE)
    seen: dict[str, bool] = {}

    def engineer(spec, me):
        crew.runner.handoff(me.id, "Dropped: the notice tidy-up was already done in t_abc.\nNothing to change.", [], "", "")
        seen["folder during the turn"] = bool(store.get_task(me.id).worktree_path)
        return [Done(final_text="Already done; dropped it.")]

    crew.script["engineer"] = engineer
    work = crew.runner.delegate(parent.id, "engineer", "tidy the notices", workspace=str(repo))
    await wait_until(crew.settled(work.id))
    task = store.get_task(work.id)
    await wait_until(lambda: not Path(task.worktree_path).exists())
    assert seen["folder during the turn"] and task.merge_state == "dropped"
    [event] = [e for e in store.events(work.id) if e.kind == "dropped"]
    assert event.data == {"by": "engineer", "reason": "the notice tidy-up was already done in t_abc."}
    assert worktree._git(repo, "rev-parse", "--verify", f"refs/heads/{task.branch}")  # the branch is kept
    assert store.owed_by_delegator("eng-lead") == []
    await crew.runner.shutdown()
