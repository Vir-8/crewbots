"""Always on: the chief looks around on its own when there's something worth a look, at the times it picks, and says
something only when there's something to say."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fakes import ScriptedCrew, bot, wait_until

from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done, Text
from crewbots.scheduler import Scheduler
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def crew_for(store, tmp_path, quiet=False):
    """A chief and an engineer, the user not mid-conversation, and the hours quiet or not as asked: what happens is
    stamped with the real clock, so the tests look around at times relative to it."""
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    crew.runner.attention.user_wrote = 0.0
    crew.runner.attention.quiet = lambda now: quiet
    return crew, Scheduler(crew.runner)


def looked(store, bot, minutes_ago):
    when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    store.set_wake(bot, looked_at=when.isoformat(timespec="milliseconds"))


def later(minutes):
    return datetime.now().astimezone() + timedelta(minutes=minutes)


def said(store, task_id):
    return [e.data for e in store.events(task_id) if e.kind == "text"]


def prompted(store, task_id):
    return [e.data["text"] for e in store.events(task_id) if e.kind == "message"]


async def test_due_wake_waits_for_cancellation_cleanup(store, tmp_path, monkeypatch):
    crew, scheduler = crew_for(store, tmp_path)
    chat = crew.runner.chat("chief")
    looked(store, "chief", 60)
    store.set_wake("chief", next_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(), next_why="review work")
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_close(_task_id):
        entered.set()
        await release.wait()

    monkeypatch.setattr(crew.runner.browser, "close_task", blocked_close)
    cancelling = asyncio.create_task(crew.runner.cancel(chat.id))
    await asyncio.wait_for(entered.wait(), 2)
    assert scheduler._wake_due(later(0)).id == chat.id
    assert await crew.runner.cancel(chat.id) == store.get_task(chat.id)
    assert not prompted(store, chat.id)
    release.set()
    await cancelling
    await wait_until(lambda: not crew.runner.running())
    assert any("review work" in text for text in prompted(store, chat.id))


async def test_news_wakes_the_chief_and_with_nothing_to_say_its_chat_shows_nothing(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path)
    crew.script["chief"] = lambda spec, me: [Text(text="NO_REPLY"), Done(final_text="NO_REPLY")]
    chat = crew.runner.chat("chief")
    looked(store, "chief", 5)
    crew.runner.happened("thread_done", "t_other", "engineer's thread 'Fix the jump' is idle")

    assert scheduler._wake_due(later(0)) is None  # news, but it looked only 5 minutes ago
    woke = scheduler._wake_due(later(20))
    await wait_until(lambda: not crew.runner.running())

    assert woke.id == chat.id
    assert "looking around on your own" in prompted(store, chat.id)[0] and "Fix the jump" in prompted(store, chat.id)[0]
    assert said(store, chat.id) == []  # NO_REPLY: nothing shown
    assert scheduler._wake_due(later(60)) is None  # nothing new since


async def test_what_it_says_unasked_shows_as_said_on_its_own(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path)
    crew.script["chief"] = lambda spec, me: [Text(text="The jump fix is in review."), Done(final_text="ok")]
    looked(store, "chief", 6)
    stopped = store.create_task("engineer", "Fix the jump", status=Status.FAILED)

    assert scheduler._wake_due(later(0)) is not None  # something failed: sooner than news alone
    await wait_until(lambda: not crew.runner.running())

    chat = crew.runner.chat("chief")
    assert said(store, chat.id) == [{"text": "The jump fix is in review.", "checkin": True}]
    assert f"{stopped.id} engineer's 'Fix the jump' is failed" in prompted(store, chat.id)[0]


async def test_it_looks_again_when_it_asked_to(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path)
    chat = crew.runner.chat("chief")
    looked(store, "chief", 0)

    reply = await CrewTools(crew.runner, chat.id, can_delegate=True).call(
        "next_check", {"minutes": 1, "why": "whether the build went green"})  # 5 at the soonest
    due = datetime.fromisoformat(store.wake_state("chief")["next_at"])

    assert "look around again at" in reply
    assert timedelta(minutes=4) < due - datetime.now(UTC) <= timedelta(minutes=5)
    assert scheduler._wake_due(due - timedelta(minutes=1)) is None
    assert scheduler._wake_due(due + timedelta(minutes=1)) is not None
    await wait_until(lambda: not crew.runner.running())
    assert "You asked to look again now: whether the build went green." in prompted(store, chat.id)[0]
    assert store.wake_state("chief")["next_at"] is None  # until it asks again
    with pytest.raises(ToolRefused, match="crew tools"):
        await CrewTools(crew.runner, crew.runner.chat("engineer").id, can_delegate=True).call(
            "next_check", {"minutes": 30, "why": "x"})


async def test_it_waits_through_quiet_hours_and_a_conversation_unless_something_failed(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path, quiet=True)
    looked(store, "chief", 60)
    crew.runner.happened("review_requested", "acme/web#4", "a review is asked for on acme/web#4")

    assert scheduler._wake_due(later(0)) is None  # quiet hours, and nothing urgent
    crew.runner.attention.quiet = lambda now: False
    crew.runner.attention.user_wrote = datetime.now().timestamp()
    assert scheduler._wake_due(later(0)) is None  # the user is mid-conversation
    crew.runner.attention.user_wrote = 0.0
    crew.runner.attention.quiet = lambda now: True
    crew.runner.happened("ci_failed", "acme/web#4", "checks failed on acme/web#4")
    assert scheduler._wake_due(later(0)) is not None  # failing checks: even in quiet hours
    await wait_until(lambda: not crew.runner.running())


async def test_only_a_coordinator_looks_around_and_not_if_its_card_says_not_to(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path)
    crew.runner.crew.bots["chief"] = crew.runner.crew.bots["chief"].__class__(
        **{**crew.runner.crew.bots["chief"].__dict__, "proactive": False})
    looked(store, "chief", 60)
    looked(store, "engineer", 60)
    crew.runner.happened("ci_failed", "acme/web#4", "checks failed on acme/web#4")

    assert scheduler._wake_due(later(0)) is None
    assert not crew.runner.always_on("engineer") and not crew.runner.always_on("chief")


def test_the_first_tick_only_starts_the_clock(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path)
    crew.runner.happened("thread_done", "t_x", "engineer's thread 'x' is idle")  # before it ever looked: not news

    assert scheduler._wake_due(later(0)) is None
    assert store.wake_state("chief")["looked_at"] is not None


def stamp_as_the_look(store, event):
    """Gives the event the very millisecond of the chief's last look."""
    store.db.execute("update crew_events set at = ? where id = ?", (store.wake_state("chief")["looked_at"], event["id"]))


async def test_an_event_in_the_millisecond_of_the_look_that_it_was_in_is_not_news_again(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path)
    crew.script["chief"] = lambda spec, me: [Text(text="NO_REPLY"), Done(final_text="NO_REPLY")]
    chat = crew.runner.chat("chief")
    looked(store, "chief", 5)
    event = crew.runner.happened("thread_done", "t_other", "engineer's thread 'Fix the jump' is idle")

    assert scheduler._wake_due(later(20)).id == chat.id
    await wait_until(lambda: not crew.runner.running())
    stamp_as_the_look(store, event)  # a tie, as on a fast machine

    assert crew.runner.news_for("chief")[:3] == ([], [], False)
    assert scheduler._wake_due(later(60)) is None  # NO_REPLY, and nothing new


async def test_an_event_written_right_after_the_look_is_news_even_in_the_same_millisecond(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path)
    crew.script["chief"] = lambda spec, me: [Text(text="NO_REPLY"), Done(final_text="NO_REPLY")]
    chat = crew.runner.chat("chief")
    looked(store, "chief", 5)
    crew.runner.happened("thread_done", "t_one", "engineer's thread 'One' is idle")
    assert scheduler._wake_due(later(20)).id == chat.id
    await wait_until(lambda: not crew.runner.running())

    later_event = crew.runner.happened("thread_done", "t_two", "engineer's thread 'Two' is idle")
    stamp_as_the_look(store, later_event)

    assert [e["subject"] for e in crew.runner.news_for("chief")[0]] == ["t_two"]
    assert scheduler._wake_due(later(60)).id == chat.id
    await wait_until(lambda: not crew.runner.running())


async def test_a_look_kept_only_as_a_time_counts_everything_up_to_it_as_seen(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path)
    crew.runner.happened("thread_done", "t_old", "engineer's thread 'Old' is idle")
    store.set_wake("chief", looked_at=(datetime.now(UTC) + timedelta(seconds=1)).isoformat(timespec="milliseconds"))

    assert crew.runner.news_for("chief")[:3] == ([], [], False)
    assert store.wake_state("chief")["seen_event"] == 1  # now kept by id
    crew.runner.happened("thread_done", "t_new", "engineer's thread 'New' is idle")
    assert [e["subject"] for e in crew.runner.news_for("chief")[0]] == ["t_new"]


async def test_a_task_that_stops_again_with_the_same_timestamp_is_news_again(store, tmp_path):
    crew, scheduler = crew_for(store, tmp_path)
    crew.script["chief"] = lambda spec, me: [Text(text="NO_REPLY"), Done(final_text="NO_REPLY")]
    chat = crew.runner.chat("chief")
    looked(store, "chief", 6)
    task = store.create_task("engineer", "Fix the jump", status=Status.FAILED)
    assert scheduler._wake_due(later(0)).id == chat.id  # seen now
    await wait_until(lambda: not crew.runner.running())
    assert crew.runner.news_for("chief")[:3] == ([], [], False)

    store.update_task(task.id, status=Status.RUNNING)
    store.update_task(task.id, status=Status.FAILED)
    store.db.execute("update tasks set updated_at = ? where id = ?", (task.updated_at, task.id))  # the same stamp

    assert [t.id for t in crew.runner.news_for("chief")[1]] == [task.id]


def test_a_waking_event_is_news_whatever_follows_it(store, tmp_path):
    crew, _ = crew_for(store, tmp_path)
    store.set_wake("chief", looked_at=datetime.now(UTC).isoformat(), seen_event=0)
    crew.runner.happened("ci_failed", "acme/web#4", "checks failed on acme/web#4")
    for n in range(250):
        crew.runner.happened("commit", f"c{n}", "a commit")  # not waking

    events, _, urgent, snap = crew.runner.news_for("chief")

    assert [e["kind"] for e in events] == ["ci_failed"] and urgent and snap == 251


def test_an_event_between_the_snapshot_and_the_marker_is_still_news(store, tmp_path):
    crew, _ = crew_for(store, tmp_path)
    store.set_wake("chief", looked_at=datetime.now(UTC).isoformat(), seen_event=0)
    crew.runner.happened("ci_failed", "acme/web#4", "checks failed on acme/web#4")
    _, _, _, snap = crew.runner.news_for("chief")
    crew.runner.happened("review_requested", "acme/web#5", "a review is asked for on acme/web#5")  # after the snapshot

    crew.runner.mark_looked("chief", snap)

    assert [e["subject"] for e in crew.runner.news_for("chief")[0]] == ["acme/web#5"]


def stop_events(store, task_id):
    return [e for e in store.crew_events() if e["kind"] == "task_stopped" and e["subject"] == task_id]


def test_metadata_updates_to_a_stopped_task_emit_no_stop_event(store):
    task = store.create_task("engineer", "Fix the jump", status=Status.FAILED)
    assert len(stop_events(store, task.id)) == 1  # created already stopped
    store.update_task(task.id, session_id="s1")
    store.update_task(task.id, session_id="s2")
    assert len(stop_events(store, task.id)) == 1


def test_a_real_transition_to_failed_emits_exactly_one_stop_event(store):
    task = store.create_task("engineer", "Fix the jump", status=Status.RUNNING)
    store.update_task(task.id, session_id="s1")
    assert stop_events(store, task.id) == []
    store.update_task(task.id, status=Status.FAILED)
    store.update_task(task.id, session_id="s2")
    assert len(stop_events(store, task.id)) == 1
