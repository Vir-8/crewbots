"""What the crew raises on its own reaches the user at one of three levels: FYI in the morning's digest, a question at
a natural break, or needs-you at once, within a daily budget and outside quiet hours."""

import time
from datetime import datetime

import pytest
from fakes import FakeBackend, bot, make_crew, wait_until
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.runner import NotAllowed, Runner
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def runner(store, tmp_path):
    r = Runner(store, make_crew(tmp_path, bot("chief", can_delegate=True), bot("engineer")), {"fake": FakeBackend()})
    r.attention.now = lambda: at(10)
    return r


def at(hour, minute=0):
    return datetime.now().astimezone().replace(hour=hour, minute=minute, second=0, microsecond=0)


def said(store, runner, bot):
    return [e.data for e in store.events(runner.chat(bot).id) if e.kind == "text"]


def test_needs_you_comes_at_once_within_a_daily_budget(store, runner):
    for n in range(4):
        runner.attention.raise_("engineer", "needs_you", f"CI on main is red ({n})", "checkin")
    delivered = said(store, runner, "engineer")
    assert [d["level"] for d in delivered] == ["needs_you", "needs_you", "needs_you", "question"]  # the fourth waits
    assert delivered[0] == {"text": "CI on main is red (0)", "checkin": True, "level": "needs_you", "proactive_id": 1}


def test_in_quiet_hours_needs_you_waits_as_a_question(store, runner):
    runner.attention.now = lambda: at(23)
    item = runner.attention.raise_("engineer", "needs_you", "CI on main is red", "checkin")
    assert item["level"] == "question" and item["delivered_at"] is None and said(store, runner, "engineer") == []
    runner.attention.tick(at(23))
    assert said(store, runner, "engineer") == []
    runner.attention.tick(at(9))  # morning, and a break: it's delivered
    assert [d["text"] for d in said(store, runner, "engineer")] == ["CI on main is red"]


async def test_a_question_waits_while_the_user_is_mid_conversation(store, runner):
    runner.say("engineer", "let's plan the release")  # the user just wrote
    await wait_until(lambda: not runner.running())
    raised = lambda: [d["text"] for d in said(store, runner, "engineer") if d.get("level")]  # noqa: E731
    runner.attention.raise_("engineer", "question", "The docs build is slow; want me to look?", "checkin")
    runner.attention.tick(at(10))
    assert raised() == []
    runner.attention.user_wrote = time.time() - 600  # ten minutes since
    runner.attention.tick(at(10))
    assert raised() == ["The docs build is slow; want me to look?"]


async def test_the_chief_tells_the_user_in_its_chat_from_a_group_chat_without_waiting(store, runner):
    room = runner.create_room("Engineering", ["chief", "engineer"], lead="chief")
    seat = store.create_task("chief", "seat", kind="seat", room_id=room["id"], status=Status.IDLE)
    runner.say("chief", "how's it going?")  # mid-conversation: what the chief tells them still shows at once
    await wait_until(lambda: not runner.running())

    runner.tell_user(seat.id, "Turn on UnityMCP for the reviewer", needs_you=True)
    assert said(store, runner, "chief")[-1] == {"text": "Turn on UnityMCP for the reviewer", "level": "needs_you",
                                                "from": "Engineering"}
    assert not store.room_messages(room["id"])
    runner.attention.now = lambda: at(23)
    assert runner.tell_user(seat.id, "Sky Islands is merged.", needs_you=True)["level"] == "question"  # no notification
    assert said(store, runner, "chief")[-1]["text"] == "Sky Islands is merged."

    section, _ = runner._said_elsewhere(runner.chat("chief"), None)  # its chat hears it, next time it runs
    assert "to the user in this chat, from Engineering: Turn on UnityMCP for the reviewer" in section
    work = store.create_task("engineer", "build", status=Status.IDLE)
    with pytest.raises(NotAllowed, match="only the chief"):
        runner.tell_user(work.id, "hi")


def test_the_morning_digest_has_the_last_days_fyis_in_the_chiefs_chat(store, runner):
    runner.attention.raise_("engineer", "fyi", "Two dependencies have new minor versions.", "checkin")
    runner.attention.raise_("chief", "fyi", "The Q3 retro draft is ready.", "checkin")
    store.add_proactive("engineer", "fyi", "Old news.", "checkin", "2000-01-01T00:00:00+00:00")  # expired
    assert said(store, runner, "engineer") == []

    runner.attention.tick(at(7))  # before the digest's hour
    assert said(store, runner, "chief") == []
    runner.attention.tick(at(8, 5))
    [digest] = said(store, runner, "chief")
    assert digest["digest"] and "- engineer: Two dependencies have new minor versions." in digest["text"]
    assert "- chief: The Q3 retro draft is ready." in digest["text"] and "Old news" not in digest["text"]
    runner.attention.tick(at(9))  # once a day
    assert len(said(store, runner, "chief")) == 1


def test_a_reaction_becomes_a_note_about_whats_worth_raising(store, runner):
    item = runner.attention.raise_("engineer", "needs_you", "The staging certificate expires Friday.", "checkin")
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        auth = {"Authorization": "Bearer t"}
        reacted = client.post(f"/proactive/{item['id']}/react", json={"reaction": "up"}, headers=auth).json()
        assert reacted["reaction"] == "up"
        assert client.post("/proactive/999/react", json={"reaction": "up"}, headers=auth).status_code == 404
        assert client.post(f"/proactive/{item['id']}/react", json={"reaction": "meh"}, headers=auth).status_code == 422
    [note] = runner.crew.memory.notes("engineer")
    assert note.title.startswith("Worth raising: The staging certificate") and "thumbs up" in note.body
