"""What the message box's commands ask of the daemon: `/new` starts a thread under the chat, `/remember` gives the
bot a note."""

from fakes import ScriptedCrew, bot, wait_until
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.events import Done
from crewbots.store import Store

AUTH = {"Authorization": "Bearer t"}


async def test_a_thread_and_a_note_from_the_message_box(tmp_path):
    store = Store(tmp_path / "crew.db")
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    crew.script["engineer"] = lambda spec, me: [Done(final_text="done: " + spec.prompt[:30])]
    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as c:
        thread = c.post("/chats/bot/engineer/threads", json={"goal": "Fix the flaky login test"}, headers=AUTH).json()
        chat = crew.runner.chat("engineer")
        assert thread["parent_id"] == chat.id and thread["created_by"] == "user" and thread["kind"] == "thread"
        assert c.post("/chats/bot/ghost/threads", json={"goal": "x"}, headers=AUTH).status_code == 404

        note = c.post("/chats/bot/engineer/notes", json={"text": "We release on Tuesdays\nNever on Fridays."},
                      headers=AUTH).json()
        assert note["title"] == "We release on Tuesdays"
        [kept] = [n for n in crew.runner.crew.memory.notes("engineer") if n.title == "We release on Tuesdays"]
        assert "Never on Fridays." in kept.body
        shown = [e.data for e in store.events(chat.id) if e.kind == "memory"]
        assert shown[-1]["by"] == "user" and shown[-1]["title"] == "We release on Tuesdays"
    await wait_until(lambda: not crew.runner.running())
    store.close()
