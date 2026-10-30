"""Results that must reach their delegator, and the stall watchdog for bots with open work."""

from datetime import UTC, datetime, timedelta

import pytest
from fakes import ScriptedCrew, bot, wait_until

from crewbots import working_state
from crewbots.backends import Access
from crewbots.crew_tools import CrewTools
from crewbots.events import Done
from crewbots.runner import MAX_REVIEW_ROUNDS
from crewbots.store import Status, Store, Verdict


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def messages(store, task_id):
    return [e.data for e in store.events(task_id) if e.kind == "message" and e.data["from"] == "crewbots"]


def notices(store, task_id):
    return [e.data["message"] for e in store.events(task_id) if e.kind == "state_notice"]


def seat_and_child(store, crew):
    room = crew.runner.create_room("Engineering", ["chief", "engineer"], lead="chief")
    seat = store.create_task("chief", "seat", kind="seat", room_id=room["id"], status=Status.IDLE)
    return seat, crew.runner.delegate(seat.id, "engineer", "build it")


async def test_a_seat_hears_a_handoff_written_after_the_turn_that_reported(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    seat, child = seat_and_child(store, crew)
    await wait_until(crew.settled(child.id))
    await wait_until(lambda: len(messages(store, seat.id)) == 1)  # the first turn ended, without a handoff
    await wait_until(crew.settled(seat.id))

    crew.runner.handoff(child.id, "checks passed", ["branch"], "", "review")  # the turn a monitor started
    await wait_until(lambda: len(messages(store, seat.id)) == 2)
    assert "checks passed" in messages(store, seat.id)[-1]["text"]
    await wait_until(crew.settled(seat.id))

    crew.runner.handoff(child.id, "round two done", [], "", "")
    await wait_until(lambda: len(messages(store, seat.id)) == 3)
    assert "round two done" in messages(store, seat.id)[-1]["text"]
    await crew.runner.shutdown()


async def test_a_handoff_during_a_running_turn_reports_once_at_its_end(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    crew.script["engineer"] = lambda spec, me: (crew.runner.handoff(me.id, "done", [], "", ""), [Done(final_text="x")])[1]
    parent = store.create_task("chief", "lead", status=Status.IDLE)
    child = crew.runner.delegate(parent.id, "engineer", "build it")
    await wait_until(crew.settled(child.id))
    await wait_until(lambda: len(messages(store, parent.id)) == 1)
    await wait_until(crew.settled(parent.id))
    assert len(messages(store, parent.id)) == 1
    await crew.runner.shutdown()


async def test_the_review_limit_reaches_the_lead_even_when_the_verdict_comes_late(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer", access=Access.WORKSPACE_WRITE),
                        bot("reviewer"))
    parent = store.create_task("chief", "lead", status=Status.IDLE)
    work = store.create_task("engineer", "build", parent_id=parent.id, created_by="chief", status=Status.IDLE)
    store.create_task("engineer", "unrelated", parent_id=parent.id, created_by="chief", status=Status.RUNNING)
    for n in range(1, MAX_REVIEW_ROUNDS + 1):
        store.update_task(work.id, turns=n)
        review = crew.runner.delegate(parent.id, "reviewer", "review it", review_of=work.id)
        await wait_until(crew.settled(review.id))
        await wait_until(crew.settled(parent.id))
        before = len(messages(store, parent.id))
        crew.runner.review(review.id, Verdict.FAIL, "no", [{"location": "a.py:1", "problem": "x"}])
        await wait_until(lambda before=before: len(messages(store, parent.id)) == before + 1)
    assert "STOP CONDITION" in messages(store, parent.id)[-1]["text"]
    await crew.runner.shutdown()


async def test_a_late_handoff_does_not_wait_for_running_siblings(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    parent = store.create_task("chief", "lead", status=Status.IDLE)
    store.create_task("engineer", "still working", parent_id=parent.id, created_by="chief", status=Status.RUNNING)
    done = store.create_task("engineer", "finished", parent_id=parent.id, created_by="chief", status=Status.IDLE)
    crew.runner.handoff(done.id, "late result", [], "", "")
    await wait_until(lambda: len(messages(store, parent.id)) == 1)
    assert "late result" in messages(store, parent.id)[0]["text"]
    await crew.runner.shutdown()


async def test_a_failure_does_not_wait_for_running_siblings(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    crew.script["engineer"] = lambda spec, me: [Done(final_text="", is_error=True, error="boom")]
    parent = store.create_task("chief", "lead", status=Status.IDLE)
    store.create_task("engineer", "still working", parent_id=parent.id, created_by="chief", status=Status.RUNNING)
    bad = crew.runner.delegate(parent.id, "engineer", "b")
    await wait_until(lambda: len(messages(store, parent.id)) == 1)
    assert store.get_task(bad.id).status is Status.FAILED
    assert bad.id in messages(store, parent.id)[0]["text"]
    assert "boom" in messages(store, parent.id)[0]["text"]
    await crew.runner.shutdown()


# The stall watchdog


def stalled_crew(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("eng-lead", can_delegate=True),
                        bot("engineer", access=Access.WORKSPACE_WRITE))
    store.set_stall_minutes(15)
    return crew


def unmerged_work(store, crew, owner="eng-lead", handoff=True):
    parent = store.create_task(owner, "lead", status=Status.IDLE)
    work = store.create_task("engineer", "build", parent_id=parent.id, created_by=owner, status=Status.IDLE)
    store.update_task(work.id, branch="crewbots/x", worktree_path="/nowhere", turns=1)
    if handoff:
        store.append_event(work.id, kind="handoff", data={})
    return parent, work


def later(minutes):
    return datetime.now(UTC) + timedelta(minutes=minutes)


def needs_me(crew, bot_name):
    path = crew.runner.crew.memory.root / bot_name / "working-state" / "state.md"
    return working_state._sections(path)["Needs me"]


async def test_open_work_wakes_twice_then_goes_to_the_chief_once_raised(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    parent, work = unmerged_work(store, crew)
    store.update_task(work.id, title="secret plan")  # handed back just now

    assert crew.runner.stall_check(later(5)) == []  # not idle long enough
    assert crew.runner.stall_check(later(20)) == ["eng-lead"]
    await wait_until(lambda: any(work.id in m for m in notices(store, parent.id)))
    await wait_until(crew.settled(parent.id))
    assert crew.runner.stall_check(later(25)) == []  # the same window: no second wake
    assert crew.runner.stall_check(later(40)) == ["eng-lead"]  # the second wake
    assert len([m for m in notices(store, parent.id) if work.id in m]) == 2
    await wait_until(crew.settled(parent.id))
    assert needs_me(crew, "chief") == [] and store.chief_events() == []  # reminders tell the chief nothing

    assert crew.runner.stall_check(later(60)) == ["eng-lead"]  # and now the chief
    [item] = needs_me(crew, "chief")
    assert "eng-lead" in item and work.id in item and "secret plan" not in item
    [event] = store.chief_events()
    assert (event["kind"], event["who"]) == ("stall raised", "eng-lead") and work.id in event["subject"]
    assert "eng-lead" not in crew.runner.stall_check(later(80))  # raised once, not again
    await crew.runner.shutdown()


async def test_a_busy_delegator_still_hears_of_work_gone_quiet(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    parent, work = unmerged_work(store, crew)
    store.db.execute("update tasks set updated_at = ? where id = ?", (later(19).isoformat(), parent.id))  # busy elsewhere

    assert crew.runner.stall_check(later(20)) == ["eng-lead"]  # timed from the work, not from the lead's session
    await crew.runner.shutdown()


async def test_a_bot_that_stopped_without_handing_back_is_woken_itself(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    parent = store.create_task("eng-lead", "lead", status=Status.IDLE)
    work = store.create_task("engineer", "fix the merge", parent_id=parent.id, created_by="eng-lead", status=Status.IDLE)
    store.append_event(work.id, kind="message", data={"from": "eng-lead", "text": "fix it"})  # in place: no branch
    assert crew.runner._next_step(store.get_task(work.id)) is None  # not the lead's to chase: nothing came back yet

    assert crew.runner.stall_check(later(20)) == ["engineer"]
    await wait_until(lambda: any("ended without handing back what eng-lead asked" in m for m in notices(store, work.id)))
    await wait_until(crew.settled(work.id))  # the reminder woke it
    assert not notices(store, parent.id)
    store.append_event(work.id, kind="handoff", data={})  # once it hands back, it's no longer stopped
    assert store.stopped_without_handback("engineer") == []
    store.append_event(work.id, kind="message", data={"from": "eng-lead", "text": "and the tests"})
    crew.runner._app_queue[("UnityMCP", "/game")] = [work.id]  # its turn for the Editor wakes it
    assert "engineer" not in crew.runner.stall_check(later(60))
    crew.runner._app_queue.clear()
    store.create_task("helper", "look it up", parent_id=work.id, created_by="engineer", status=Status.RUNNING)
    assert store.stopped_without_handback("engineer") == []  # waiting on work it handed out
    await crew.runner.shutdown()


async def test_open_work_follows_the_lifecycle_events(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    parent, work = unmerged_work(store, crew, handoff=False)
    assert crew.runner.stall_check(later(20)) == []  # no handoff yet
    owed = lambda: [t.id for t in store.owed_by_delegator("eng-lead")]  # noqa: E731
    store.append_event(work.id, kind="handoff", data={})
    assert owed() == [work.id]
    store.append_event(work.id, kind="review", data={"verdict": "fail"})  # the delegator's to send back
    assert owed() == [work.id]
    store.append_event(work.id, kind="handoff", data={})
    store.append_event(work.id, kind="review", data={"verdict": "pass"})
    assert owed() == [work.id]
    for closing in ("merge_withdrawn", "worktree_dropped", "merged"):
        store.append_event(work.id, kind="handoff", data={})
        assert owed() == [work.id]
        store.append_event(work.id, kind=closing, data={})
        assert owed() == []
    await crew.runner.shutdown()


async def test_a_dropped_task_is_not_open_work(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    parent, work = unmerged_work(store, crew)
    assert [t.id for t in store.owed_by_delegator("eng-lead")] == [work.id]
    crew.runner._record(work.id, kind="worktree_dropped", data={})
    assert store.owed_by_delegator("eng-lead") == []
    assert crew.runner.stall_check(later(60)) == []
    await crew.runner.shutdown()


async def test_each_delegating_session_is_looked_at_on_its_own(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    waiting, work_a = unmerged_work(store, crew)
    stalled, work_b = unmerged_work(store, crew)
    store.update_task(waiting.id, status=Status.NEEDS_INPUT)  # waiting on Vir: not a stall

    assert crew.runner.stall_check(later(20)) == ["eng-lead"]
    assert notices(store, waiting.id) == []
    await wait_until(lambda: any(work_b.id in m for m in notices(store, stalled.id)))
    await wait_until(crew.settled(stalled.id))
    assert crew.runner.stall_check(later(40)) == ["eng-lead"]
    await wait_until(crew.settled(stalled.id))
    assert crew.runner.stall_check(later(60)) == ["eng-lead"]
    [item] = needs_me(crew, "chief")
    assert work_b.id in item and work_a.id not in item
    assert notices(store, waiting.id) == []
    await crew.runner.shutdown()


async def test_a_needs_me_item_alone_is_open_work_and_its_text_is_not_copied(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    working_state.edit(crew.runner.crew.memory.root / "eng-lead" / "working-state" / "state.md", "Needs me", "add",
                       "reply to Vir about the secret")
    chat = crew.runner.chat("eng-lead")
    assert crew.runner.stall_check(later(20)) == ["eng-lead"]
    [notice] = notices(store, chat.id)
    assert "1 Needs-me item" in notice and "secret" not in notice
    assert "Your reply here goes to the user" in notice and "NO_REPLY" in notice  # its chat is the user's
    tools = CrewTools(crew.runner, chat.id, can_delegate=True)
    assert "reply to Vir about the secret" in await tools.call("working_state", {})  # a look, with no action
    assert "reply to Vir about the secret" in await tools.call("working_state", {"section": "Needs me", "action": "list"})
    crew.runner.stall_check(later(40))
    crew.runner.stall_check(later(60))
    assert "secret" not in " ".join(needs_me(crew, "chief"))
    await crew.runner.shutdown()


async def test_a_room_mention_is_its_seats_to_answer_and_waits_while_the_seat_is_on_it(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    room = crew.runner.create_room("Engineering", ["eng-lead", "engineer"], lead="eng-lead")
    seat = store.create_task("engineer", "Engineering (engineer)", kind="seat", room_id=room["id"], status=Status.IDLE)
    chat = crew.runner.chat("engineer")
    store.db.execute("update tasks set updated_at = '2020-01-01T00:00:00+00:00' where id in (?, ?)", (seat.id, chat.id))
    mention = {"author": "eng-lead", "at": datetime.now(UTC).isoformat(), "id": 7}
    working_state.room_mentions(crew.runner.crew.memory.root / "engineer" / "working-state" / "state.md", room,
                                [mention], add=True)

    assert crew.runner.stall_check(later(1)) == []  # just said: not overdue because the bot's chat has been quiet
    crew.runner._runs[seat.id] = object()  # the seat is answering it
    assert crew.runner.stall_check(later(20)) == []
    del crew.runner._runs[seat.id]

    assert crew.runner.stall_check(later(20)) == ["engineer"]
    said = [m["text"] for m in store.room_messages(room["id"]) if m["author"] == "crewbots"]
    assert said == [f"@engineer Still open for you, with nothing run for a while: the @mention here from eng-lead at "
                    f"{working_state.timefmt.minute(mention['at'])}, which you haven't answered. Pick it up, or say "
                    "what is blocking it."]
    assert notices(store, chat.id) == []  # not in its chat with the user, which can't see the room
    await crew.runner.shutdown()


async def test_no_open_work_does_not_wake(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    store.create_task("eng-lead", "lead", status=Status.IDLE)
    parent, work = unmerged_work(store, crew)
    store.update_task(work.id, merge_state="merged")
    assert crew.runner.stall_check(later(60)) == []
    assert notices(store, parent.id) == []


async def test_a_running_stopped_or_muted_bot_is_not_woken(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    parent, work = unmerged_work(store, crew)
    crew.runner._runs[parent.id] = object()  # running
    assert crew.runner.stall_check(later(20)) == []
    del crew.runner._runs[parent.id]

    store.update_task(parent.id, status=Status.CANCELLED)  # stopped by the user
    assert crew.runner.stall_check(later(20)) == []
    store.update_task(parent.id, status=Status.IDLE)

    store.set_chat_muted("eng-lead", True)
    assert crew.runner.stall_check(later(20)) == []
    assert notices(store, parent.id) == []
    await crew.runner.shutdown()


async def test_a_target_that_is_escalated_or_queued_is_not_woken_until_idle(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    parent, work = unmerged_work(store, crew)
    crew.runner._escalated.add(parent.id)  # escalated in a turn that hasn't ended
    assert crew.runner.stall_check(later(20)) == []
    crew.runner._escalated.discard(parent.id)
    crew.runner._waiting[parent.id] = ["queued message"]
    assert crew.runner.stall_check(later(20)) == []
    del crew.runner._waiting[parent.id]
    assert notices(store, parent.id) == []
    store.db.execute("update tasks set updated_at = '2020-01-01T00:00:00+00:00' where id = ?", (parent.id,))
    assert crew.runner.stall_check(later(20)) == ["eng-lead"]
    await crew.runner.shutdown()


async def test_another_session_of_the_bot_does_not_delay_or_block_a_stalled_group(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    parent, work = unmerged_work(store, crew)
    other = store.create_task("eng-lead", "something else", status=Status.RUNNING)
    crew.runner._runs[other.id] = object()  # another session of the bot is running and keeps changing
    store.db.execute("update tasks set updated_at = '2020-01-01T00:00:00+00:00' where id in (?, ?)", (parent.id, work.id))
    for minutes in (20, 40, 60):
        store.update_task(other.id, title=f"still going {minutes}")  # fresh activity in the other session
        assert crew.runner.stall_check(later(minutes)) == ["eng-lead"]
        await wait_until(crew.settled(parent.id))
    assert len(notices(store, parent.id)) == 2
    [item] = needs_me(crew, "chief")
    assert work.id in item
    del crew.runner._runs[other.id]
    await crew.runner.shutdown()


async def test_waiting_on_the_user_is_the_status_or_a_pending_permission(store, tmp_path):
    crew = stalled_crew(store, tmp_path)
    parent, work = unmerged_work(store, crew)
    store.update_task(parent.id, status=Status.NEEDS_INPUT)
    assert crew.runner.stall_check(later(20)) == []

    # an escalation the delegating bot answered: the status is back to idle, so it isn't waiting
    store.update_task(parent.id, status=Status.IDLE)
    store.append_event(parent.id, kind="escalation", data={"question": "which?"})
    assert not crew.runner.waiting_on_user(store.get_task(parent.id))
    assert crew.runner.stall_check(later(20)) == ["eng-lead"]

    chat = crew.runner.chat("eng-lead")
    store.ask_permission(chat.id, "network", "", "get the web", "needed")
    assert crew.runner.waiting_on_user(chat)
    store.append_event(chat.id, kind="questions_closed", data={})
    assert not crew.runner.waiting_on_user(chat)
    await crew.runner.shutdown()


def reviewed(store, crew, parent, work, verdict):
    review = store.create_task("reviewer", "review", parent_id=parent.id, created_by="eng-lead", review_of=work.id,
                               review_version=work.turns, status=Status.IDLE)
    crew.runner.review(review.id, verdict, f"{verdict}", [])
    return review


async def test_each_reminder_names_the_next_step(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("eng-lead", can_delegate=True), bot("engineer"), bot("reviewer"))
    step = lambda work: crew.runner._next_step(store.get_task(work.id))  # noqa: E731
    parent, work = unmerged_work(store, crew)
    assert step(work) == f"{work.id} (engineer) was handed back: have it reviewed (delegate with review_of), or drop it"
    running = store.create_task("reviewer", "review", parent_id=parent.id, created_by="eng-lead", review_of=work.id,
                                review_version=1, status=Status.RUNNING)
    assert step(work) is None  # its reviewer is at it
    store.update_task(running.id, status=Status.IDLE)
    assert "has a review that ended without a verdict" in step(work)
    crew.runner.review(running.id, Verdict.FAIL, "no", [])
    assert step(work) == (f"{work.id} (engineer) failed review round 1 of {MAX_REVIEW_ROUNDS}: send back the findings "
                          "you judge real with message, or drop it")
    for version in range(2, MAX_REVIEW_ROUNDS + 1):
        store.update_task(work.id, turns=version)
        reviewed(store, crew, parent, store.get_task(work.id), Verdict.FAIL)
    assert step(work) is None  # out of rounds: the chief hears of it, and it's the user's
    _, approved = unmerged_work(store, crew)
    reviewed(store, crew, parent, store.get_task(approved.id), Verdict.PASS)
    assert step(approved) == f"{approved.id} (engineer) passed every review: propose it with propose_merge"
    asking = store.create_task("engineer", "build", parent_id=parent.id, created_by="eng-lead", status=Status.NEEDS_INPUT)
    assert [t.id for t in store.asking_delegator("eng-lead")] == [asking.id]
    assert step(asking) == f"{asking.id} (engineer) asked you a question: answer it with message"
    store.ask_permission(asking.id, "network", "", "get the web", "needed")
    assert store.asking_delegator("eng-lead") == []  # that one waits on the user
    await crew.runner.shutdown()


async def test_work_handed_out_in_a_group_chat_is_chased_there(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("eng-lead", can_delegate=True),
                        bot("engineer"))
    store.set_stall_minutes(15)
    room = crew.runner.create_room("Engineering", ["chief", "eng-lead", "engineer"], lead="eng-lead")
    seat = store.create_task("eng-lead", "Engineering (eng-lead)", kind="seat", room_id=room["id"], status=Status.IDLE)
    work = store.create_task("engineer", "build", parent_id=seat.id, created_by="eng-lead", status=Status.IDLE)
    store.update_task(work.id, branch="crewbots/x", worktree_path="/nowhere", turns=1)
    store.append_event(work.id, kind="handoff", data={})
    said = lambda: [(m["author"], m["text"]) for m in store.room_messages(room["id"])]  # noqa: E731

    assert crew.runner.stall_check(later(20)) == ["eng-lead"]
    assert said()[-1] == ("crewbots", f"@eng-lead Still open for you, with nothing run for a while: {work.id} (engineer) "
                                      "was handed back: have it reviewed (delegate with review_of), or drop it. Pick it "
                                      "up, or say what is blocking it.")
    await wait_until(lambda: any("Still open for you" in s.prompt for s in crew.specs_for("eng-lead")))
    await wait_until(crew.settled(seat.id))
    crew.runner.stall_check(later(40))
    await wait_until(crew.settled(seat.id))
    assert crew.runner.stall_check(later(60)) == ["eng-lead"]
    assert said()[-1][0] == "crewbots" and said()[-1][1].startswith("@chief @eng-lead went quiet with open work")
    await wait_until(lambda: any("went quiet" in s.prompt for s in crew.specs_for("chief")))
    assert needs_me(crew, "chief") == [] and store.chief_events() == []  # raised where the work is, not twice
    await crew.runner.shutdown()
