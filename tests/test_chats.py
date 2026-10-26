"""Chats, threads and group chats: each bot's one ongoing conversation, the work it opens, and bots talking together."""

import pytest
from fakes import ScriptedCrew, bot, wait_until
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.crew import NO_REPLY
from crewbots.crew_tools import CrewTools
from crewbots.events import Done
from crewbots.runner import MAX_ROOM_DEPTH, NotAllowed
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def crew(store, tmp_path):
    return ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer"), bot("reviewer"))


def idle(crew):
    return lambda: not crew.runner.running()


# A bot's chat.


async def test_each_bot_has_one_chat_that_carries_on(crew, store):
    first = crew.runner.say("chief", "hello")
    await wait_until(idle(crew))
    again = crew.runner.say("chief", "and again")
    await wait_until(idle(crew))

    assert first.id == again.id == crew.runner.chat("chief").id and first.kind == "chat"
    assert [e.data["text"] for e in store.events(first.id) if e.kind == "message"] == ["hello", "and again"]
    assert "ongoing chat with the user" in crew.specs_for("chief")[0].system_prompt


async def test_a_bot_opens_a_thread_for_real_work_and_hears_back_in_its_chat(crew, store):
    chat = crew.runner.chat("engineer")
    opened = []
    crew.script["engineer"] = lambda spec, me: (
        [Done(final_text="on it")] if me.kind != "chat" or opened else
        [Done(final_text=opened.append(crew.runner.start_thread(me.id, "Add CSV export", "Add a CSV export.")) or "opened")])

    crew.runner.say("engineer", "add CSV export")
    await wait_until(lambda: len(opened) == 1 and idle(crew)())

    thread = opened[0]
    assert thread.kind == "thread" and thread.parent_id == chat.id and thread.title == "Add CSV export"
    reports = [e.data["text"] for e in store.events(chat.id) if e.kind == "message" and e.data["from"] == "crewbots"]
    assert reports and thread.id in reports[0]  # the result came back to the chat, which woke the bot
    assert [t.id for t in store.threads_of("engineer")] == [thread.id]


# Group chats.


def replies(crew, by_bot):
    """Each bot's reply in a room, by name; a bot not listed has nothing to add."""
    for name in ("chief", "engineer", "reviewer"):
        crew.script[name] = lambda spec, me, name=name: [Done(final_text=by_bot.get(name, NO_REPLY))]


async def test_the_lead_answers_a_message_that_mentions_no_one(crew, store):
    room = crew.runner.create_room("Reports", ["chief", "engineer"], lead="chief")
    replies(crew, {"chief": "On it.", "engineer": "me too"})

    crew.runner.post(room["id"], "user", "Can we ship CSV export this week?")
    await wait_until(idle(crew))

    assert [(m["author"], m["text"]) for m in store.room_messages(room["id"])] == [
        ("user", "Can we ship CSV export this week?"), ("chief", "On it.")]
    assert "lead it" in crew.specs_for("chief")[0].system_prompt


async def test_mentions_wake_those_bots_and_bots_bring_each_other_in(crew, store):
    room = crew.runner.create_room("Reports", ["chief", "engineer", "reviewer"], lead="chief")
    replies(crew, {"engineer": "Done. @reviewer can you check it?", "reviewer": "Looks right."})

    crew.runner.post(room["id"], "user", "@engineer add the endpoint")
    await wait_until(idle(crew))

    assert [m["author"] for m in store.room_messages(room["id"])] == ["user", "engineer", "reviewer"]
    reviewer_saw = crew.specs_for("reviewer")[0].prompt
    assert "[user]: @engineer add the endpoint" in reviewer_saw and "[engineer]: Done." in reviewer_saw


async def test_bots_stop_waking_each_other_a_few_replies_deep(crew, store):
    room = crew.runner.create_room("Loop", ["engineer", "reviewer"], lead="engineer")
    replies(crew, {"engineer": "@reviewer your turn", "reviewer": "@engineer no, yours"})

    crew.runner.post(room["id"], "user", "go")
    await wait_until(idle(crew), timeout=10)

    bot_messages = [m for m in store.room_messages(room["id"]) if m["author"] != "user"]
    assert len(bot_messages) == MAX_ROOM_DEPTH and bot_messages[-1]["depth"] == MAX_ROOM_DEPTH


async def test_a_bot_posts_to_the_crews_room_from_its_own_chat(crew, store):
    replies(crew, {"reviewer": "Sure, send it over."})
    chat = crew.runner.chat("engineer")
    tools = CrewTools(crew.runner, chat.id, can_delegate=False)

    assert (await tools.call("post", {"text": "@reviewer can you look at the CSV branch?"})).startswith("Posted")
    await wait_until(idle(crew))

    crew_room = crew.runner.crew_room()
    assert crew_room["members"] == ["chief", "engineer", "reviewer"] and crew_room["lead"] == "chief"
    assert [m["author"] for m in store.room_messages(crew_room["id"])] == ["engineer", "reviewer"]
    with pytest.raises(NotAllowed, match="isn't in the group chat"):
        other = crew.runner.create_room("Private", ["chief"])
        crew.runner.post_from(chat.id, "hi", other["id"])


async def test_a_bot_opens_a_group_chat_and_brings_a_teammate_in(crew, store):
    chat = crew.runner.chat("engineer")
    tools = CrewTools(crew.runner, chat.id, can_delegate=False)
    with crew.runner.bus.subscribe() as queue:
        said = await tools.call("open_group", {"title": "CSV export", "members": ["reviewer"]})
        room = queue.get_nowait()["room"]  # the app shows it at once
    assert room["members"] == ["engineer", "reviewer"] and room["lead"] == "engineer" and room["id"] in said

    replies(crew, {"reviewer": "Pulling chief in. @chief does this need sign-off?", "chief": "No, ship it."})
    seat = store.create_task("reviewer", "CSV export (reviewer)", kind="seat", room_id=room["id"], status=Status.IDLE)
    assert "chief is in" in await CrewTools(crew.runner, seat.id, can_delegate=False).call("add_to_group", {"bot": "chief"})
    assert store.room(room["id"])["members"] == ["engineer", "reviewer", "chief"]

    crew.runner.post(room["id"], "user", "@reviewer is this good to go?")
    await wait_until(idle(crew))
    assert [m["author"] for m in store.room_messages(room["id"])] == ["user", "reviewer", "chief"]


def test_group_chats_bots_open_stay_small_and_only_members_bring_others_in(crew, store):
    chief = crew.runner.chat("chief").id
    room = crew.runner.open_group(chief, "Plan", ["engineer"], lead="engineer")
    assert room["lead"] == "engineer"
    with pytest.raises(NotAllowed, match="besides you"):
        crew.runner.open_group(chief, "Alone", ["chief"])
    with pytest.raises(NotAllowed, match="only someone in the group chat"):
        crew.runner.add_to_group(crew.runner.chat("reviewer").id, "reviewer", room["id"])
    with pytest.raises(NotAllowed, match="say which group chat"):
        crew.runner.add_to_group(chief, "reviewer")
    with pytest.raises(NotAllowed, match="answering a teammate's question"):
        crew.runner.open_group(store.create_task("engineer", "Q", kind="ask").id, "Side", ["reviewer"])
    with pytest.raises(NotAllowed, match="at most"):
        crew.runner.open_group(chief, "Everyone", [f"bot{i}" for i in range(6)])


# Over the API.


def test_the_app_sees_every_conversation_with_what_is_unread(crew, store):
    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        auth = {"Authorization": "Bearer t"}
        chats = client.get("/chats", headers=auth).json()
        assert [c["key"] for c in chats][:3] == ["bot:chief", "bot:engineer", "bot:reviewer"]
        assert chats[3]["kind"] == "group" and chats[3]["title"] == "Crew"

        task = client.post("/chats/bot/engineer/messages", json={"text": "hi"}, headers=auth).json()
        for _ in range(200):
            if client.get(f"/tasks/{task['id']}", headers=auth).json()["status"] == Status.IDLE:
                break
        engineer = next(c for c in client.get("/chats", headers=auth).json() if c["key"] == "bot:engineer")
        assert engineer["unread"] == 0 and engineer["last"]["author"] == "user"  # the fake says nothing back as text

        client.post("/chats/bot:engineer/read", headers=auth).raise_for_status()
        room = client.post("/rooms", json={"title": "Pair", "members": ["engineer", "reviewer"]}, headers=auth).json()
        assert client.post(f"/rooms/{room['id']}/messages", json={"text": "hello"}, headers=auth).status_code == 201
        assert client.get(f"/rooms/{room['id']}/messages", headers=auth).json()[0]["text"] == "hello"
        profile = client.get("/bots/engineer/profile", headers=auth).json()
        assert profile["bot"] == "engineer" and profile["threads"] == []
        assert client.post("/rooms", json={"members": ["nobody"]}, headers=auth).status_code == 422
