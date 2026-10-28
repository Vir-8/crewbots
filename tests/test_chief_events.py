"""Crew activity batched into one short notice for the chief's main chat."""

from datetime import UTC, datetime, timedelta

import pytest
from fakes import ScriptedCrew, bot, wait_until

from crewbots.backends import Access
from crewbots.events import Done
from crewbots.runner import MAX_REVIEW_ROUNDS, MergeError
from crewbots.store import Status, Store, Verdict

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def make(store, tmp_path, access=Access.WORKSPACE_WRITE):
    return ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("eng-lead", can_delegate=True),
                        bot("engineer", access=access), bot("reviewer"))


def notices(store, crew):
    return [e.data["message"] for e in store.events(crew.runner.chat("chief").id) if e.kind == "state_notice"]


async def test_other_bots_events_in_the_window_make_one_notice_with_ids(store, tmp_path):
    crew = make(store, tmp_path, Access.READ_ONLY)
    parent = store.create_task("eng-lead", "lead", status=Status.IDLE)
    ok = crew.runner.delegate(parent.id, "engineer", "a")
    await wait_until(crew.settled(ok.id))
    crew.script["engineer"] = lambda spec, me: [Done(final_text="", is_error=True, error="boom")]
    bad = crew.runner.delegate(parent.id, "engineer", "b")
    await wait_until(lambda: store.get_task(bad.id).status is Status.FAILED)

    assert crew.runner.flush_chief_events(T0)
    [text] = notices(store, crew)
    assert f"done: {ok.id}" in text and f"failed: {bad.id}" in text and "boom" not in text
    assert not crew.runner.flush_chief_events(T0 + timedelta(seconds=10))  # nothing new
    await crew.runner.shutdown()


async def test_a_late_handoff_is_listed_again(store, tmp_path):
    crew = make(store, tmp_path, Access.READ_ONLY)
    parent = store.create_task("eng-lead", "lead", status=Status.IDLE)
    child = crew.runner.delegate(parent.id, "engineer", "a")
    await wait_until(crew.settled(child.id))
    await wait_until(crew.settled(parent.id))
    assert crew.runner.flush_chief_events(T0)
    crew.runner.handoff(child.id, "later", [], "", "")
    await wait_until(crew.settled(crew.runner.chat("chief").id))
    assert crew.runner.flush_chief_events(T0 + timedelta(minutes=3))
    assert f"done: {child.id}" in notices(store, crew)[-1]
    await crew.runner.shutdown()


async def test_one_notice_per_window_and_none_when_quiet(store, tmp_path):
    crew = make(store, tmp_path)
    assert not crew.runner.flush_chief_events(T0)
    assert notices(store, crew) == []
    crew.runner._crew_event("escalation", "t_a", "engineer")
    assert crew.runner.flush_chief_events(T0)
    await wait_until(crew.settled(crew.runner.chat("chief").id))
    crew.runner._crew_event("escalation", "t_b", "engineer")
    assert not crew.runner.flush_chief_events(T0 + timedelta(seconds=60))  # inside the window: kept
    assert crew.runner.flush_chief_events(T0 + timedelta(seconds=130))
    assert len(notices(store, crew)) == 2 and "t_b" in notices(store, crew)[1]
    await crew.runner.shutdown()


async def test_the_events_and_the_throttle_survive_a_restart(store, tmp_path):
    crew = make(store, tmp_path)
    crew.runner._crew_event("merged", "t_a", "engineer")
    assert crew.runner.flush_chief_events(T0)
    await wait_until(crew.settled(crew.runner.chat("chief").id))
    crew.runner._crew_event("merged", "t_b", "engineer")
    await crew.runner.shutdown()

    again = make(store, tmp_path)  # a new runner on the same database
    assert not again.runner.flush_chief_events(T0 + timedelta(seconds=30))
    assert [e["subject"] for e in store.chief_events()] == ["t_b"]
    assert again.runner.flush_chief_events(T0 + timedelta(seconds=130))
    assert store.chief_events() == []
    await again.runner.shutdown()


async def test_a_failed_notice_keeps_the_batch_and_the_time(store, tmp_path, monkeypatch):
    crew = make(store, tmp_path)
    crew.runner._crew_event("merged", "t_a", "engineer")

    def broken(bot, text):
        raise OSError("no")

    monkeypatch.setattr(crew.runner, "_state_notice", broken)
    assert not crew.runner.flush_chief_events(T0)
    assert len(store.chief_events()) == 1 and store.routine_state("chief-events") is None
    monkeypatch.undo()
    assert crew.runner.flush_chief_events(T0)
    assert store.chief_events() == []
    await crew.runner.shutdown()


async def test_a_busy_chief_chat_leaves_the_batch_in_the_store_until_it_is_idle(store, tmp_path):
    crew = make(store, tmp_path)
    chat = crew.runner.chat("chief")
    crew.runner._crew_event("merged", "t_a", "engineer")
    for stop in (lambda: store.update_task(chat.id, status=Status.CANCELLED),
                 lambda: store.update_task(chat.id, status=Status.NEEDS_INPUT),
                 lambda: store.update_task(chat.id, status=Status.IDLE) and store.ask_permission(
                     chat.id, "network", "", "the web", "needed")):
        stop()
        assert not crew.runner.flush_chief_events(T0)
        assert len(store.chief_events()) == 1 and notices(store, crew) == []
        assert crew.runner._held_notices == {}
    store.db.execute("update permissions set allowed = 1")
    store.update_task(chat.id, status=Status.IDLE)
    assert crew.runner.flush_chief_events(T0)
    assert "merged: t_a" in notices(store, crew)[0] and store.chief_events() == []
    await crew.runner.shutdown()


async def test_an_escalation_mid_turn_keeps_the_batch_until_vir_has_answered_and_the_chat_is_idle(store, tmp_path):
    crew = make(store, tmp_path)
    chat = crew.runner.chat("chief")
    crew.runner._crew_event("merged", "t_a", "engineer")
    store.update_task(chat.id, status=Status.RUNNING)
    crew.runner._runs[chat.id] = object()
    crew.runner._escalated.add(chat.id)  # the chief escalated in this turn; its status is still running
    assert not crew.runner.flush_chief_events(T0)
    store.update_task(chat.id, status=Status.NEEDS_INPUT)  # the turn ended, waiting on Vir
    del crew.runner._runs[chat.id]
    assert not crew.runner.flush_chief_events(T0)
    assert len(store.chief_events()) == 1 and notices(store, crew) == []

    store.update_task(chat.id, status=Status.IDLE)  # Vir answered
    crew.runner._escalated.discard(chat.id)
    assert crew.runner.flush_chief_events(T0)
    assert "merged: t_a" in notices(store, crew)[0] and store.chief_events() == []
    await crew.runner.shutdown()


async def test_a_chat_in_an_idle_flush_or_with_waiting_messages_keeps_the_batch(store, tmp_path):
    crew = make(store, tmp_path)
    chat = crew.runner.chat("chief")
    crew.runner._crew_event("merged", "t_a", "engineer")
    crew.runner._runs[chat.id] = object()  # a run or an idle flush holds the chat, whatever its status says
    assert not crew.runner.flush_chief_events(T0)
    del crew.runner._runs[chat.id]
    crew.runner._waiting[chat.id] = ["a message"]
    assert not crew.runner.flush_chief_events(T0)
    del crew.runner._waiting[chat.id]
    store.update_task(chat.id, status=Status.QUEUED)
    assert not crew.runner.flush_chief_events(T0)
    assert len(store.chief_events()) == 1 and notices(store, crew) == []
    store.update_task(chat.id, status=Status.IDLE)
    assert crew.runner.flush_chief_events(T0) and store.chief_events() == []
    await crew.runner.shutdown()


async def test_the_chiefs_own_tasks_are_not_listed(store, tmp_path):
    crew = make(store, tmp_path)
    crew.runner._crew_event("done", "t_a", "chief")
    assert not crew.runner.flush_chief_events(T0)
    await crew.runner.shutdown()


async def test_verdicts_limits_escalations_merges_and_stalls_are_listed(store, tmp_path):
    crew = make(store, tmp_path)
    parent = store.create_task("eng-lead", "lead", status=Status.IDLE)
    work = store.create_task("engineer", "build", parent_id=parent.id, created_by="eng-lead", status=Status.IDLE)
    for n in range(1, MAX_REVIEW_ROUNDS + 1):
        store.update_task(work.id, turns=n)
        review = crew.runner.delegate(parent.id, "reviewer", "review it", review_of=work.id)
        await wait_until(crew.settled(review.id))
        crew.runner.review(review.id, Verdict.FAIL, "no", [])
    with pytest.raises(MergeError):
        crew.runner.merge(work.id)
    crew.runner.escalate(work.id, "which way?")
    crew.runner._crew_event("stall raised", work.id, "eng-lead")

    assert crew.runner.flush_chief_events(T0)
    text = notices(store, crew)[-1]
    for part in (f"review fail: {work.id}", f"review limit: {work.id}", f"merge failed: {work.id}",
                 f"escalation: {work.id}", f"stall raised: {work.id} (eng-lead)"):
        assert part in text
    await crew.runner.shutdown()
