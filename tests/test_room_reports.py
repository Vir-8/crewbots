"""Work handed out in a group chat is talked about there, in each bot's own words, as well as reaching whoever it's
for: the handover, questions and answers, fix requests, and what comes back."""

import pytest
from fakes import ScriptedCrew, bot, wait_until

from crewbots.events import Done
from crewbots.store import Status, Store, Verdict


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def reports(store, task_id):
    return [e.data["text"] for e in store.events(task_id) if e.kind == "message" and e.data["from"] == "crewbots"]


def said_by(store, room, author):
    return [m for m in store.room_messages(room["id"]) if m["author"] == author]


def crew_in_a_room(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("lead", can_delegate=True), bot("engineer"), bot("reviewer"),
                        bot("outsider"))
    room = crew.runner.create_room("Engineering", ["lead", "engineer", "reviewer"], lead="lead")
    seat = store.create_task("lead", "Engineering (lead)", kind="seat", room_id=room["id"], status=Status.IDLE)
    return crew, room, seat


async def test_finished_work_is_said_in_the_room_by_its_bot_without_waking_anyone_twice(store, tmp_path):
    crew, room, seat = crew_in_a_room(store, tmp_path)
    crew.script["engineer"] = lambda spec, me: (
        crew.runner.handoff(me.id, "Built the export. @reviewer may want a look.", [], "CSV quoting", ""),
        [Done(final_text="done")])[1]
    child = crew.runner.delegate(seat.id, "engineer", "build the export")
    await wait_until(lambda: reports(store, seat.id))
    assert said_by(store, room, "lead")[0]["text"] == f"@engineer {child.id}: build the export"
    assert store.seat_of(room["id"], "engineer") is None  # the handover wakes no seat: the work runs in its thread
    [post] = said_by(store, room, "engineer")
    assert post["text"] == f"@lead {child.id}: Built the export. @reviewer may want a look.\n\nStill open: CSV quoting"
    assert store.seen(f"seat:{seat.id}") >= post["id"]  # the full report tells the lead; the post isn't sent again
    assert "Built the export" in reports(store, seat.id)[0]
    await wait_until(crew.settled(seat.id))
    assert not crew.specs_for("reviewer")  # a mention in a report wakes no one
    await crew.runner.shutdown()


async def test_a_review_verdict_and_its_findings_are_said_in_the_room_by_the_reviewer(store, tmp_path):
    crew, room, seat = crew_in_a_room(store, tmp_path)
    work = store.create_task("engineer", "build", parent_id=seat.id, created_by="lead", status=Status.IDLE)
    store.update_task(work.id, turns=1, reported=True)
    found = [{"location": "export.py:12", "problem": "Quotes are dropped.", "scenario": "A name with a comma splits."},
             {"location": "", "problem": "No test covers quoting.", "scenario": "", "proof": ""}]
    crew.script["reviewer"] = lambda spec, me: (
        crew.runner.review(me.id, Verdict.FAIL, "The CSV export drops quotes.", found), [Done(final_text="fail")])[1]
    review = crew.runner.delegate(seat.id, "reviewer", "review it", review_of=work.id)
    await wait_until(lambda: said_by(store, room, "reviewer"))
    [post] = said_by(store, room, "reviewer")
    assert post["text"] == (f"@lead {review.id}, a review of {work.id}: FAIL. The CSV export drops quotes.\n\nFindings:"
                            "\n- `export.py:12`: Quotes are dropped. A name with a comma splits.\n- No test covers quoting.")
    await crew.runner.shutdown()


async def test_work_by_a_bot_outside_the_room_reports_only_to_whoever_handed_it_out(store, tmp_path):
    crew, room, seat = crew_in_a_room(store, tmp_path)
    crew.runner.delegate(seat.id, "outsider", "look it up")
    await wait_until(lambda: reports(store, seat.id))
    assert not said_by(store, room, "outsider")
    await crew.runner.shutdown()


async def test_code_work_is_handed_back_with_its_evidence_and_findings_with_their_proof(store, tmp_path):
    from crewbots.crew_tools import CrewTools, ToolRefused

    crew, room, seat = crew_in_a_room(store, tmp_path)
    work = store.create_task("engineer", "build", parent_id=seat.id, created_by="lead", status=Status.IDLE)
    store.update_task(work.id, branch="crewbots/x", turns=1)
    tools = CrewTools(crew.runner, work.id, can_delegate=False)
    with pytest.raises(ToolRefused, match="evidence"):  # code work isn't handed back on its word alone
        await tools.call("handoff", {"done": "Built the export"})
    await tools.call("handoff", {"done": "Built the export", "evidence": [
        "scripts/verify webkit … --js export.js → rows: 3, header: id,name", "couldn't check: Safari download prompt"]})
    review = crew.runner.delegate(seat.id, "reviewer", "review it", review_of=work.id)
    crew.runner.review(review.id, Verdict.FAIL, "Quotes break it.", [
        {"location": "export.py:12", "problem": "commas unquoted", "proof": "export([\"a,b\"]) → 'a,b' (2 columns)"},
        {"location": "export.py:30", "problem": "may be slow", "proof": ""}])
    await wait_until(lambda: any("Review of" in r for r in reports(store, seat.id)))
    text = "\n".join(reports(store, seat.id))
    assert "Evidence:\n- scripts/verify webkit" in text and "- couldn't check: Safari download prompt" in text
    assert "Proof: export([\"a,b\"])" in text and "may be slow (not reproduced)" in text
    await crew.runner.shutdown()


async def test_a_handback_is_said_as_soon_as_its_turn_ends_while_a_sibling_still_works(store, tmp_path):
    crew, room, seat = crew_in_a_room(store, tmp_path)
    store.create_task("reviewer", "still going", parent_id=seat.id, created_by="lead", status=Status.RUNNING)
    child = crew.runner.delegate(seat.id, "engineer", "build the export")
    await wait_until(lambda: said_by(store, room, "engineer"))
    await wait_until(crew.settled(child.id))
    assert said_by(store, room, "engineer")[0]["text"].startswith(f"@lead {child.id}: engineer did:")
    assert not reports(store, seat.id)  # the lead hears once its other work is done too
    await crew.runner.shutdown()


async def test_questions_answers_and_fix_requests_are_said_in_the_room(store, tmp_path):
    crew, room, seat = crew_in_a_room(store, tmp_path)
    plan = store.create_task("lead", "plan", parent_id=seat.id, created_by="lead", status=Status.IDLE)
    crew.script["engineer"] = lambda spec, me: (
        crew.runner.escalate(me.id, "CSV or JSON?", ["CSV", "JSON"]) if me.turns == 0 else None,
        [Done(final_text="asked" if me.turns == 0 else "Used CSV.")])[1]
    work = crew.runner.delegate(plan.id, "engineer", "build the export")  # from a thread under the seat
    await wait_until(lambda: store.get_task(work.id).status is Status.NEEDS_INPUT)
    crew.runner.message_child(plan.id, work.id, "CSV, please")
    await wait_until(lambda: any("Used CSV" in m["text"] for m in said_by(store, room, "engineer")))
    crew.script["reviewer"] = lambda spec, me: [Done(final_text="Yes: every field is quoted.")]
    crew.runner.ask(seat.id, "reviewer", "Is the CSV quoted?")
    await wait_until(lambda: said_by(store, room, "reviewer"))
    said = [(m["author"], m["text"]) for m in store.room_messages(room["id"])]
    assert said[:2] == [("lead", f"@engineer {work.id}: build the export"),
                        ("engineer", f"@lead {work.id} needs an answer: CSV or JSON?\n\nChoices:\n- CSV\n- JSON")]
    assert ("lead", f"@engineer {work.id}: CSV, please") in said
    assert ("engineer", f"@lead {work.id}: Used CSV.") in said
    asked = said.index(("lead", "@reviewer Is the CSV quoted?"))
    assert said.index(("reviewer", "@lead Yes: every field is quoted.")) > asked
    await crew.runner.shutdown()


async def test_work_handed_out_outside_a_group_chat_stays_out_of_it(store, tmp_path):
    crew, room, seat = crew_in_a_room(store, tmp_path)
    chat = crew.runner.chat("lead")
    child = crew.runner.delegate(chat.id, "engineer", "build the export")
    await wait_until(crew.settled(child.id))
    assert store.room_messages(room["id"]) == []
    await crew.runner.shutdown()


def test_a_woken_bot_is_sent_the_newest_of_what_it_missed():
    from crewbots.runner.rooms import CATCH_UP, _catch_up

    said = [f"[engineer]: {n} " + "x" * 3000 for n in range(10)] + ["[user]: @chief how's it going?"]
    text = _catch_up(said)
    assert text.startswith("(7 earlier messages you haven't seen are left out.)\n\n[engineer]: 7 ")
    assert text.endswith("[user]: @chief how's it going?") and len(text) <= CATCH_UP + 100
    assert _catch_up(["[user]: " + "y" * (CATCH_UP * 2)]) == "[user]: " + "y" * (CATCH_UP * 2)  # the newest always


async def test_a_long_handover_is_said_in_the_room_whole(store, tmp_path):
    crew, room, seat = crew_in_a_room(store, tmp_path)
    goal = "Build the export. " + "Here is what matters and why. " * 100
    child = crew.runner.delegate(seat.id, "engineer", goal)
    assert said_by(store, room, "lead")[0]["text"] == f"@engineer {child.id}: {goal}"
    await crew.runner.shutdown()
