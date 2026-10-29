"""Work handed out in a group chat reports there, in its own bot's words, as well as to whoever handed it out."""

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
    [post] = said_by(store, room, "engineer")
    assert post["text"] == f"@lead {child.id}: Built the export. @reviewer may want a look.\n\nStill open: CSV quoting"
    assert store.seen(f"seat:{seat.id}") >= post["id"]  # the full report tells the lead; the post isn't sent again
    assert "Built the export" in reports(store, seat.id)[0]
    await wait_until(crew.settled(seat.id))
    assert not crew.specs_for("reviewer")  # a mention in a report wakes no one
    await crew.runner.shutdown()


async def test_a_review_verdict_is_said_in_the_room_by_the_reviewer(store, tmp_path):
    crew, room, seat = crew_in_a_room(store, tmp_path)
    work = store.create_task("engineer", "build", parent_id=seat.id, created_by="lead", status=Status.IDLE)
    store.update_task(work.id, turns=1, reported=True)
    crew.script["reviewer"] = lambda spec, me: (
        crew.runner.review(me.id, Verdict.FAIL, "The CSV export drops quotes.", []), [Done(final_text="fail")])[1]
    crew.runner.delegate(seat.id, "reviewer", "review it", review_of=work.id)
    await wait_until(lambda: said_by(store, room, "reviewer"))
    [post] = said_by(store, room, "reviewer")
    assert post["text"] == f"@lead Review of {work.id}: FAIL. The CSV export drops quotes."
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
