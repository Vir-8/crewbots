"""What a bot says in a group chat keeps the folder the task that said it works in, so the app can find the files its
relative links name. The user's messages and crewbots' own have none."""

import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.backends import Access
from crewbots.crew_tools import CrewTools
from crewbots.events import Done
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def crew(store, tmp_path, studio):
    return ScriptedCrew(store, tmp_path, bot("artist", Access.WORKSPACE_WRITE, workspace=studio), bot("critic"))


def folders(store, room):
    return [(m["author"], m["folder"]) for m in store.room_messages(room["id"])]


async def test_a_seats_posts_reply_and_shared_files_keep_the_folder_it_works_in(crew, store, studio):
    (studio / "cut.mp4").write_bytes(b"\0" * 64)

    def turn(spec, me):
        crew.runner.post_from(me.id, "The plan's in [plan](docs/plan.md).")
        return [Done(final_text="And the [cut](cut.mp4).")]

    crew.script["artist"] = turn
    room = crew.runner.create_room("Launch", ["artist", "critic"], "artist")
    crew.runner.post(room["id"], "user", "where are we?")
    await wait_until(lambda: len(folders(store, room)) == 3)
    seat = store.seat_of(room["id"], "artist")
    await CrewTools(crew.runner, seat.id, can_delegate=False).call("share", {"files": ["cut.mp4"]})
    await wait_until(lambda: not crew.runner.running())

    assert folders(store, room) == [("user", None), *[("artist", str(studio))] * 3]


async def test_a_seats_first_reply_keeps_the_worktree_its_turn_made(store, tmp_path):
    repo = git_repo(tmp_path.parent / f"{tmp_path.name}-repo")
    crew = ScriptedCrew(store, tmp_path, bot("eng", Access.WORKSPACE_WRITE, workspace=repo), bot("critic"))
    crew.script["eng"] = lambda spec, me: [Done(final_text="See [plan](docs/plan.md).")]
    room = crew.runner.create_room("Launch", ["eng", "critic"], "eng")
    crew.runner.post(room["id"], "user", "where are we?")
    await wait_until(lambda: len(folders(store, room)) == 2)
    await wait_until(lambda: not crew.runner.running())

    seat = store.seat_of(room["id"], "eng")
    assert seat.worktree_path and folders(store, room)[1] == ("eng", seat.worktree_path)
    await crew.runner.shutdown()


async def test_a_post_from_a_thread_keeps_the_threads_worktree(crew, store, tmp_path):
    room = crew.runner.create_room("Launch", ["artist", "critic"], "artist")
    thread = store.create_task("artist", "cut the trailer", status=Status.IDLE)
    store.update_task(thread.id, worktree_path=str(tmp_path / "wts" / thread.id))

    with crew.runner.bus.subscribe() as live:
        crew.runner.post_from(thread.id, "Cut: [it](renders/cut.mp4).", room["id"])
        sent = live.get_nowait()

    assert sent["type"] == "room_message" and sent["message"]["folder"] == str(tmp_path / "wts" / thread.id)
    assert folders(store, room) == [("artist", str(tmp_path / "wts" / thread.id))]
    await crew.runner.shutdown()


async def test_a_seat_asking_its_lead_keeps_its_folder(crew, store, studio):
    room = crew.runner.create_room("Launch", ["artist", "critic"], "artist")
    seat = store.create_task("critic", "Launch (critic)", kind="seat", room_id=room["id"], status=Status.IDLE)

    crew.runner.escalate(seat.id, "Loud or quiet?")

    [asked] = store.room_messages(room["id"])
    assert asked["author"] == "critic" and asked["folder"] == str(crew.runner.crew.folder_of(crew.runner.crew.bots["critic"]))
    await crew.runner.shutdown()


def test_the_api_sends_a_bots_folder_and_none_for_the_users(crew, store, tmp_path):
    room = crew.runner.create_room("Launch", ["artist", "critic"], "artist")
    thread = store.create_task("artist", "cut the trailer", status=Status.IDLE)
    store.update_task(thread.id, worktree_path=str(tmp_path / "wt"))
    crew.runner.post_from(thread.id, "See [plan](docs/plan.md).", room["id"])

    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        auth = {"Authorization": "Bearer t"}
        posted = client.post(f"/rooms/{room['id']}/messages", json={"text": "thanks"}, headers=auth).json()
        said = client.get(f"/rooms/{room['id']}/messages", headers=auth).json()

    assert posted["folder"] is None
    assert [(m["author"], m["folder"]) for m in said[:2]] == [("artist", str(tmp_path / "wt")), ("user", None)]
