"""What commands ask of the daemon: the message box's `/new` starts a thread under the chat and `/remember` gives
the bot a note, and `crewbots watch` follows a task and everything under it."""

from fakes import ScriptedCrew, bot, wait_until
from fastapi.testclient import TestClient

from crewbots import cli
from crewbots.api import create_app
from crewbots.events import Done
from crewbots.store import Status, Store

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


async def test_watch_follows_an_old_chat_and_everything_under_it(tmp_path, capsys):
    store = Store(tmp_path / "crew.db")
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    chat = store.create_task("engineer", "chat", kind="chat", status=Status.IDLE)
    thread = store.create_task("engineer", "Fix the flaky login test", parent_id=chat.id, status=Status.IDLE)
    check = store.create_task("engineer", "Read the CI log", parent_id=thread.id, status=Status.IDLE)
    for task, text in [(chat, "how is the login test?"), (thread, "on it"), (check, "the log is green")]:
        store.append_event(task.id, kind="message", data={"from": "user", "text": text})
    for n in range(1000):  # the chat is older than the newest thousand tasks
        store.create_task("engineer", f"later thread {n}", status=Status.IDLE)
    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1", headers=AUTH) as c:
        assert cli._follow(c, chat.id) == 0
    out, err = capsys.readouterr()
    assert out.splitlines() == ["", "» user: how is the login test?", f"[engineer {thread.id}] ", "» user: on it",
                                f"[engineer {check.id}] ", "» user: the log is green"]
    assert err == f"· {chat.id} is idle\n"
    store.close()
