"""Images and files attached to a message: kept in the crew's folder, shown with the message, and listed with their
paths in what the bot gets, so it opens them itself."""

import base64

import pytest
from fakes import FakeBackend, make_crew, wait_until
from fastapi.testclient import TestClient

from crewbots import attachments
from crewbots.api import create_app
from crewbots.runner import Runner
from crewbots.store import Status, Store


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "crew.db")
    backend = FakeBackend()
    runner = Runner(store, make_crew(tmp_path), {"fake": backend}, worktree_root=tmp_path / "wts")
    yield runner, store, backend, tmp_path
    store.close()


def test_an_attachment_is_kept_in_the_crews_folder_under_a_safe_name(tmp_path):
    shot = attachments.save(tmp_path, "../../Screen Shot 1.png", b"\x89PNG")

    assert shot["kind"] == "image" and shot["mime"] == "image/png" and shot["name"] == "Screen Shot 1.png"
    assert shot["path"].startswith(str(tmp_path / "attachments" / shot["id"]))
    assert attachments.find(tmp_path, shot["id"]) == shot
    picked = tmp_path / "notes.pdf"
    picked.write_bytes(b"%PDF")
    assert attachments.copy_in(tmp_path, picked)["mime"] == "application/pdf"
    with pytest.raises(attachments.AttachmentError):
        attachments.find(tmp_path, "../etc")


async def test_the_bot_gets_the_message_with_its_attachments_listed(world):
    runner, store, backend, home = world
    shot = attachments.save(home, "error.png", b"\x89PNG")

    task = runner.say("worker", "What's wrong here?", attachments=[shot])
    await wait_until(lambda: store.get_task(task.id).status is Status.IDLE)

    prompt = backend.specs[0].prompt
    assert prompt.startswith("What's wrong here?") and f"error.png (image, 1 KB): {shot['path']}" in prompt
    message = next(e for e in store.events(task.id) if e.kind == "message")
    assert message.data == {"from": "user", "text": "What's wrong here?", "attachments": [shot]}  # shown as it was sent
    assert str(home / "attachments") in [str(d) for d in backend.specs[0].read_dirs]


async def test_a_group_chat_message_keeps_its_attachments(world):
    runner, store, _, home = world
    doc = attachments.save(home, "brief.md", b"# Brief")
    room = runner.create_room("Launch", ["worker"], lead="worker")

    posted = runner.post(room["id"], "user", "Read this", attachments=[doc])

    assert posted["attachments"] == [doc] and store.room_messages(room["id"])[0]["attachments"] == [doc]
    seat = store.seat_of(room["id"], "worker")
    await wait_until(lambda: store.get_task(seat.id).status is Status.IDLE)
    assert doc["path"] in runner.backends["fake"].specs[-1].prompt  # the lead's seat is told where it is


def test_the_app_uploads_one_then_sends_it(world):
    runner, store, _, _ = world
    auth = {"Authorization": "Bearer t"}
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765") as client:
        up = client.post("/v1/attachments", headers=auth,
                         json={"name": "a.png", "data": base64.b64encode(b"\x89PNG").decode()}).json()
        assert client.get(f"/v1/attachments/{up['id']}", headers=auth).json()["data"] == base64.b64encode(b"\x89PNG").decode()

        sent = client.post("/v1/chats/bot/worker/messages", headers=auth, json={"attachments": [up["id"]]})
        assert sent.status_code == 200
        assert client.post("/v1/chats/bot/worker/messages", headers=auth, json={"text": " "}).status_code == 422
        assert client.post("/v1/chats/bot/worker/messages", headers=auth,
                           json={"text": "x", "attachments": ["nope"]}).status_code == 422
