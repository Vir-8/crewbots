"""Managing group chats: renaming one, changing who's in it and who leads, muting, archiving and deleting it."""

import pytest
from fakes import ScriptedCrew, bot, make_crew, wait_until
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.crew_tools import CrewTools
from crewbots.events import Done
from crewbots.runner import NotAllowed
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


async def test_a_group_chat_is_renamed_and_its_bots_and_lead_changed(crew, store):
    room = crew.runner.create_room("Reports", ["chief", "engineer"], lead="chief")
    seat = store.create_task("engineer", "Reports (engineer)", kind="seat", room_id=room["id"], status=Status.IDLE)
    with crew.runner.bus.subscribe() as queue:
        changed = await crew.runner.change_room(room["id"], title="Exports", members=["engineer", "reviewer"],
                                                lead="reviewer")
        assert queue.get_nowait() == {"type": "room", "room": changed}  # the app hears at once
    assert (changed["title"], changed["members"], changed["lead"]) == ("Exports", ["engineer", "reviewer"], "reviewer")
    assert store.get_task(seat.id).title == "Exports (engineer)"

    with pytest.raises(NotAllowed, match="its lead is one of its bots"):
        await crew.runner.change_room(room["id"], members=["engineer"])  # without the lead, and no new one named
    with pytest.raises(NotAllowed, match="needs a name"):
        await crew.runner.change_room(room["id"], title="  ")
    assert (await crew.runner.change_room(room["id"], members=["engineer"], lead="engineer"))["lead"] == "engineer"


async def test_an_archived_group_chat_keeps_its_history_and_its_bots_stay_quiet_there(crew, store):
    room = crew.runner.create_room("Old plans", ["chief", "engineer"], lead="chief")
    crew.script["chief"] = lambda spec, me: [Done(final_text="Noted.")]
    crew.runner.post(room["id"], "user", "first")
    await wait_until(idle(crew))

    archived = await crew.runner.change_room(room["id"], archived=True, muted=True)
    assert archived["archived_at"] and archived["muted"] and len(store.room_messages(room["id"])) == 2
    with pytest.raises(NotAllowed, match="is archived"):
        crew.runner.post_from(crew.runner.chat("engineer").id, "hello?", room["id"])

    crew.runner.post(room["id"], "user", "back to this")  # saying something there brings it back
    await wait_until(idle(crew))
    assert store.room(room["id"])["archived_at"] is None and store.room_messages(room["id"])[-1]["text"] == "Noted."


async def test_deleting_a_group_chat_keeps_the_work_it_handed_out(crew, store):
    room = crew.runner.create_room("Side project", ["engineer", "reviewer"], lead="engineer")
    opened = []
    crew.script["engineer"] = lambda spec, me: [Done(final_text="ok" if me.kind != "seat" or opened else
                                                     opened.append(crew.runner.start_thread(me.id, "Spike", "Try it."))
                                                     or "Opened a thread for it.")]
    crew.runner.post(room["id"], "user", "can you spike this?")
    await wait_until(lambda: opened and idle(crew)())
    seat = store.seat_of(room["id"], "engineer")

    with crew.runner.bus.subscribe() as queue:
        await crew.runner.delete_room(room["id"])
        heard = [queue.get_nowait() for _ in range(queue.qsize())]
    assert heard[-1] == {"type": "room_gone", "room_id": room["id"]}
    assert {"type": "task", "task": store.get_task(opened[0].id).to_json()} in heard
    assert room["id"] not in [r["id"] for r in store.rooms()] and store.room_messages(room["id"]) == []
    assert store.get_task(opened[0].id).parent_id == crew.runner.chat("engineer").id  # it reports to the chat now
    assert store.get_task(seat.id).kind == "seat"  # kept, for what its turns cost


async def test_the_crews_own_group_chat_stays_and_takes_in_new_bots(crew, store, tmp_path):
    crew_room = crew.runner.crew_room()
    await crew.runner.change_room(crew_room["id"], title="Everyone")
    assert crew.runner.crew_room()["id"] == crew_room["id"]  # found by what it is, not by its name
    with pytest.raises(NotAllowed, match="can't be archived"):
        await crew.runner.change_room(crew_room["id"], archived=True)
    with pytest.raises(NotAllowed, match="can't be deleted"):
        await crew.runner.delete_room(crew_room["id"])

    crew.runner.reload(make_crew(tmp_path, *crew.runner.crew.bots.values(), bot("critic")))
    assert crew.runner.crew_room()["members"] == ["chief", "engineer", "reviewer", "critic"]


async def test_bots_can_post_to_the_crews_room_once_it_is_renamed(crew, store):
    await crew.runner.change_room(crew.runner.crew_room()["id"], title="All hands")
    tools = CrewTools(crew.runner, crew.runner.chat("engineer").id, can_delegate=False)
    assert (await tools.call("post", {"text": "hi all"})).startswith("Posted")
    assert store.room_messages(crew.runner.crew_room()["id"])[-1]["text"] == "hi all"


def test_the_app_manages_group_chats(crew, store):
    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        auth = {"Authorization": "Bearer t"}
        room = client.post("/rooms", json={"title": "Pair", "members": ["engineer", "reviewer"]}, headers=auth).json()
        assert [r["id"] for r in client.get("/rooms", headers=auth).json()] == [room["id"]]

        changed = client.patch(f"/rooms/{room['id']}", json={"title": "Duo", "lead": "reviewer", "muted": True,
                                                              "max_depth": 3}, headers=auth).json()
        assert (changed["title"], changed["lead"], changed["muted"], changed["max_depth"]) == ("Duo", "reviewer", True, 3)
        assert client.patch(f"/rooms/{room['id']}", json={"members": ["nobody"]}, headers=auth).status_code == 422
        assert client.patch(f"/rooms/{room['id']}", json={"lead": "chief"}, headers=auth).status_code == 403

        crew_room = next(c["room"] for c in client.get("/chats", headers=auth).json() if c["kind"] == "group"
                         and c["room"]["crew"])
        assert client.delete(f"/rooms/{crew_room['id']}", headers=auth).status_code == 403
        assert client.delete(f"/rooms/{room['id']}", headers=auth).status_code == 204
        assert client.delete(f"/rooms/{room['id']}", headers=auth).status_code == 404


def test_an_older_crews_room_called_crew_becomes_its_own(tmp_path):
    path = tmp_path / "crew.db"
    store = Store(path)
    crew_room = store.create_room("Crew", "chief", ["chief"])
    store.create_room("Crew", "chief", ["chief"])  # a later one with the same name stays an ordinary group chat
    store.db.execute("update rooms set crew = 0")
    store.db.execute("pragma user_version = 2")  # from before rooms were marked
    store.close()

    again = Store(path)
    assert [r["id"] for r in again.rooms() if r["crew"]] == [crew_room["id"]]
    again.close()
