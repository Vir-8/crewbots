"""Always on: the chief looks around on its own when there's something worth a look, at the times it picks, and says
something only when there's something to say."""

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
    with pytest.raises(ToolRefused, match="coordinator"):
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
