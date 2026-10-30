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
        c.runner = runner
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


def test_worktree_maintenance_is_admin_only(client):
    assert client.post("/worktrees/sweep").status_code == 403
    assert client.post("/tasks/t_nope/drop-worktree").status_code == 403
    task = client.runner.store.create_task("worker", "task")
    client.runner._tokens["bot-token"] = task.id
    bot_auth = {"Authorization": "Bearer bot-token"}
    assert client.post("/worktrees/sweep", headers=bot_auth).status_code == 403
    assert client.post(f"/tasks/{task.id}/drop-worktree", headers=bot_auth).status_code == 403
    assert client.post("/worktrees/sweep", headers=AUTH).json() == {"removed": [], "kept": []}
    assert client.post("/tasks/t_nope/drop-worktree", headers=AUTH).status_code == 404


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
        backlog = [ws.receive_json() for _ in range(6)]
        assert [m["event"]["kind"] for m in backlog] == ["message", "turn_start", "session_started", "text", "usage", "done"]

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


def test_the_daemon_says_which_version_it_is(client):
    from crewbots import typescript
    from crewbots.api import API_VERSION, openapi_schema

    version = client.get("/version", headers=AUTH).json()

    assert version["api"] == API_VERSION and version["build"]  # this checkout's commit
    assert version["schema_hash"] == typescript.schema_hash(openapi_schema())  # what the app is built with
    assert f'API_SCHEMA = "{version["schema_hash"]}"' in typescript.from_openapi(openapi_schema())
    assert version["source_changed"] is False


def test_the_apps_errors_go_to_the_daemons_log_without_flooding_it(client, capsys):
    for n in range(40):
        assert client.post("/client-errors", json={"where": "a message", "message": f"boom {n}", "stack": "at X"},
                           headers=AUTH).status_code == 204

    logged = capsys.readouterr().err
    assert "app: a message failed: boom 0\nat X" in logged
    assert logged.count("app: a message failed") == 30  # a minute's worth; the rest are dropped


def test_the_crew_can_be_paused_and_resumed_over_the_api(client):
    unknown = "running (its plan's limits aren't known yet)"
    assert client.get("/crew", headers=AUTH).json() == {
        "paused": False, "restarting": False, "running": 0, "turns_running": 0, "held": 0, "sessions": 0, "limits": [],
        "landed": 0,
        "background": [{"plan": "claude", "go": True, "reason": unknown}, {"plan": "codex", "go": True, "reason": unknown}]}

    assert client.post("/crew/pause", headers=AUTH).json()["paused"] is True
    assert client.get("/crew", headers=AUTH).json()["background"][0]["reason"] == "waiting while the crew is paused"
    task = client.post("/tasks", json={"bot": "worker", "goal": "x"}, headers=AUTH).json()
    time.sleep(0.05)
    assert client.get(f"/tasks/{task['id']}", headers=AUTH).json()["status"] == "queued"

    assert client.post("/crew/resume", headers=AUTH).json()["paused"] is False
    assert wait_idle(client, task["id"])["status"] == "idle"


def test_a_restart_waits_for_the_crew_to_be_idle_then_stops_the_server(tmp_path):
    import asyncio

    from crewbots.api import RESTART_EXIT

    gate = asyncio.Event()
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path), {"fake": FakeBackend(gate=gate)})
    app = create_app(runner, TOKEN, PORT)
    stopped = []
    app.state.stop = stopped.append
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}/v1") as client:
        busy = client.post("/tasks", json={"bot": "worker", "goal": "long"}, headers=AUTH).json()
        for _ in range(100):
            if client.get(f"/tasks/{busy['id']}", headers=AUTH).json()["status"] == "running":
                break
            time.sleep(0.02)

        state = client.post("/crew/restart", json={"timeout_s": 60}, headers=AUTH).json()
        assert state["restarting"] and state["turns_running"] == 1 and not state["paused"]
        later = client.post("/tasks", json={"bot": "worker", "goal": "later"}, headers=AUTH).json()
        time.sleep(0.05)
        assert stopped == [] and client.get(f"/tasks/{later['id']}", headers=AUTH).json()["status"] == "queued"

        client.portal.call(gate.set)  # the running turn finishes
        for _ in range(300):
            if stopped:
                break
            time.sleep(0.01)
        assert stopped == [RESTART_EXIT]
        assert client.get(f"/tasks/{later['id']}", headers=AUTH).json()["status"] == "queued"  # carried on after
    store.close()


def test_a_restart_can_be_called_off(client):
    assert client.post("/crew/restart", json={"timeout_s": 60}, headers=AUTH).json()["restarting"] is True
    state = client.delete("/crew/restart", headers=AUTH).json()
    assert state["restarting"] is False and state["paused"] is False
    task = client.post("/tasks", json={"bot": "worker", "goal": "x"}, headers=AUTH).json()
    assert wait_idle(client, task["id"])["status"] == "idle"


def test_the_crews_record_can_be_searched(client):
    task = client.post("/tasks", json={"bot": "worker", "goal": "Add a pagination cursor"}, headers=AUTH).json()
    wait_idle(client, task["id"])

    hits = client.get("/search", params={"q": "pagination"}, headers=AUTH).json()

    assert hits and hits[0]["task_id"] == task["id"] and "«pagination»" in hits[0]["snippet"]
