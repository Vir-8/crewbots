import time

import pytest
from fakes import FakeBackend, make_crew
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from crewbots.api import create_app
from crewbots.runner import Runner
from crewbots.store import Store

TOKEN = "test-token"
PORT = 8765
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WS_HOST = {"Host": f"127.0.0.1:{PORT}"}  # the test client sends "testserver" on WebSockets otherwise


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path), {"fake": FakeBackend()})
    with TestClient(create_app(runner, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}/v1") as c:
        yield c
    store.close()


def wait_idle(client, task_id):
    for _ in range(200):
        task = client.get(f"/tasks/{task_id}", headers=AUTH).json()
        if task["status"] not in ("queued", "running"):
            return task
        time.sleep(0.02)
    raise TimeoutError(task_id)


def test_every_request_needs_the_token(client):
    assert client.get("/bots").status_code == 403
    assert client.get("/bots", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/bots", headers=AUTH).status_code == 200


def test_a_page_reaching_localhost_under_another_host_name_is_refused(client):
    assert client.get("/bots", headers={**AUTH, "Host": "evil.example:8765"}).status_code == 403


def test_a_task_from_start_to_follow_up(client):
    assert client.get("/bots", headers=AUTH).json()[0]["name"] == "worker"

    created = client.post("/tasks", json={"bot": "worker", "goal": "hello"}, headers=AUTH)
    assert created.status_code == 201
    task = wait_idle(client, created.json()["id"])
    assert task["result"] == "did: hello"

    client.post(f"/tasks/{task['id']}/messages", json={"text": "again"}, headers=AUTH).raise_for_status()
    task = wait_idle(client, task["id"])
    assert task["result"] == "did: again"

    events = client.get(f"/tasks/{task['id']}/events", headers=AUTH).json()
    assert [e["data"]["text"] for e in events if e["kind"] == "message"] == ["hello", "again"]
    assert client.get(f"/tasks/{task['id']}/changes", headers=AUTH).json() == {"diff": None}
    assert [t["id"] for t in client.get("/tasks", params={"status": "idle"}, headers=AUTH).json()] == [task["id"]]


def test_mistakes_get_clear_errors(client):
    assert client.post("/tasks", json={"bot": "ghost", "goal": "x"}, headers=AUTH).status_code == 422
    assert client.get("/tasks/t_nope", headers=AUTH).status_code == 404
    assert client.post("/tasks/t_nope/messages", json={"text": "x"}, headers=AUTH).status_code == 404


def test_live_sends_the_backlog_then_new_updates(client):
    first = client.post("/tasks", json={"bot": "worker", "goal": "one"}, headers=AUTH).json()
    wait_idle(client, first["id"])

    with client.websocket_connect(f"/v1/live?token={TOKEN}", headers=WS_HOST) as ws:
        backlog = [ws.receive_json() for _ in range(5)]
        assert [m["event"]["kind"] for m in backlog] == ["message", "session_started", "text", "usage", "done"]

        client.post("/tasks", json={"bot": "worker", "goal": "two"}, headers=AUTH)
        live = []
        while not (live and live[-1]["type"] == "task" and live[-1]["task"]["status"] == "idle"):
            live.append(ws.receive_json())
        texts = [m["event"]["data"].get("text") for m in live if m["type"] == "event" and m["event"]["kind"] == "text"]
        assert texts == ["did: two"]


def test_live_refuses_another_host_name(client):
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/v1/live?token={TOKEN}") as ws:
        ws.receive_json()


def test_live_needs_the_token_too(client):
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/v1/live?token=wrong", headers=WS_HOST) as ws:
        ws.receive_json()


def test_the_event_cursor_lets_a_client_skip_the_history(client):
    assert client.get("/events/cursor", headers=AUTH).json() == {"id": 0}
    task = client.post("/tasks", json={"bot": "worker", "goal": "one"}, headers=AUTH).json()
    wait_idle(client, task["id"])

    cursor = client.get("/events/cursor", headers=AUTH).json()["id"]

    assert cursor == client.get(f"/tasks/{task['id']}/events", headers=AUTH).json()[-1]["id"]


def test_every_route_is_versioned_and_the_daemon_serves_no_docs(client):
    from crewbots.api import openapi_schema

    assert all(path.startswith("/v1/") for path in openapi_schema()["paths"])
    for docs in ("/openapi.json", "/docs", "/redoc"):  # generated offline; nothing extra to reach
        assert client.get(f"http://127.0.0.1:{PORT}{docs}", headers=AUTH).status_code == 404


def test_the_apps_types_match_the_api():
    """app/src/api-types.ts is generated from the schema; regenerate with `npm run types` in app/ when this fails."""
    from pathlib import Path

    from crewbots import typescript
    from crewbots.api import openapi_schema

    committed = Path(__file__).parent.parent / "app" / "src" / "api-types.ts"
    assert committed.read_text() == typescript.from_openapi(openapi_schema()), "app/src/api-types.ts is stale"


def test_the_crew_can_be_paused_and_resumed_over_the_api(client):
    assert client.get("/crew", headers=AUTH).json() == {"paused": False, "running": 0, "sessions": 0}

    assert client.post("/crew/pause", headers=AUTH).json()["paused"] is True
    task = client.post("/tasks", json={"bot": "worker", "goal": "x"}, headers=AUTH).json()
    time.sleep(0.2)
    assert client.get(f"/tasks/{task['id']}", headers=AUTH).json()["status"] == "queued"

    assert client.post("/crew/resume", headers=AUTH).json()["paused"] is False
    assert wait_idle(client, task["id"])["status"] == "idle"


def test_the_crews_record_can_be_searched(client):
    task = client.post("/tasks", json={"bot": "worker", "goal": "Add a pagination cursor"}, headers=AUTH).json()
    wait_idle(client, task["id"])

    hits = client.get("/search", params={"q": "pagination"}, headers=AUTH).json()

    assert hits and hits[0]["task_id"] == task["id"] and "«pagination»" in hits[0]["snippet"]
