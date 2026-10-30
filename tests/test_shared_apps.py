"""A shared app's MCP server (a Unity Editor's, say) is reached through the daemon, which lets one task's tool calls
through at a time: another's are answered that it's busy, and it's told when the app is free."""

import json
import time

import httpx
import pytest
from fakes import bot, make_crew
from fastapi.testclient import TestClient
from test_bot_api import ADMIN, PORT, HeldBackend

from crewbots import crew as crew_module
from crewbots import mcp_catalog
from crewbots.api import create_app
from crewbots.api.routes import shared_apps as route
from crewbots.backends import Access, McpServer
from crewbots.runner import Runner
from crewbots.runner.shared_apps import APP_IDLE_S
from crewbots.store import Store

UNITY = McpServer(url="http://unity.test/mcp", transport="http", one_at_a_time=True)


@pytest.fixture
def app(tmp_path, monkeypatch):
    reached = []

    def unity(request):
        sent = json.loads(request.content or b"null")
        reached.append(sent["method"] if sent else request.method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": sent and sent.get("id"), "result": {"ok": True}},
                              headers={"mcp-session-id": "s1"})

    monkeypatch.setattr(route, "TRANSPORT", httpx.MockTransport(unity))
    store = Store(tmp_path / "crew.db")
    backend = HeldBackend()
    engineers = [bot(f"engineer-{n}", access=Access.WORKSPACE_WRITE, mcp={"UnityMCP": UNITY}) for n in (1, 2)]
    runner = Runner(store, make_crew(tmp_path / "crew", bot("chief", can_delegate=True), *engineers), {"fake": backend},
                    api_url=f"http://127.0.0.1:{PORT}/v1")
    with TestClient(create_app(runner, ADMIN, PORT), base_url=f"http://127.0.0.1:{PORT}/v1") as client:
        yield client, runner, store, backend, reached
        backend.release.set()
    store.close()


def running(client, backend, name, folder=None):
    """A task of `name`'s, held mid-turn in `folder`, and how it reaches Unity."""
    body = {"bot": name, "goal": "go"} | ({"workspace": str(folder)} if folder else {})
    task = client.post("/tasks", json=body, headers={"Authorization": f"Bearer {ADMIN}"}).json()
    for _ in range(200):
        for spec in backend.specs:
            if f"You are the {name}." in spec.system_prompt:
                return task["id"], spec.mcp_servers["UnityMCP"]
        time.sleep(0.01)
    raise TimeoutError(name)


def call(client, server, method, id=1):
    return client.post(server.url.removeprefix(f"http://127.0.0.1:{PORT}/v1"), headers=server.headers,
                       json={"jsonrpc": "2.0", "id": id, "method": method, "params": {}})


def test_one_task_at_a_time_drives_a_projects_app(app, tmp_path):
    client, runner, store, backend, reached = app
    game = tmp_path / "game"
    game.mkdir()
    first, through_first = running(client, backend, "engineer-1", game)
    second, through_second = running(client, backend, "engineer-2", game)
    assert through_first.url == f"http://127.0.0.1:{PORT}/v1/bot/mcp/UnityMCP" and through_first.url == through_second.url
    assert through_first.headers["Authorization"] != through_second.headers["Authorization"]  # each as its own task

    answer = call(client, through_first, "tools/call")
    assert answer.json()["result"] == {"ok": True} and answer.headers["mcp-session-id"] == "s1"
    busy = call(client, through_second, "tools/call", id=7).json()
    assert busy["id"] == 7 and busy["result"]["isError"]
    assert f"UnityMCP is in use for game by engineer-1 ({first})" in busy["result"]["content"][0]["text"]
    assert call(client, through_second, "tools/list").json()["result"] == {"ok": True}  # looking never waits
    assert reached == ["tools/call", "tools/list"]

    runner._apps["UnityMCP", str(game.resolve())]["last"] -= APP_IDLE_S  # the first has left it alone
    runner.free_apps()
    told = [e.data["text"] for e in store.events(second) if e.kind == "message" and e.data["from"] == "crewbots"]
    assert told == ["UnityMCP is free now for game: go on with what needed it."]
    assert call(client, through_second, "tools/call").json()["result"] == {"ok": True}
    assert "in use for game by engineer-2" in call(client, through_first, "tools/call").json()["result"]["content"][0]["text"]


def test_tasks_in_different_projects_drive_their_own_editors_at_once(app, tmp_path):
    client, runner, store, backend, reached = app
    (tmp_path / "game").mkdir()
    (tmp_path / "other").mkdir()
    _, through_first = running(client, backend, "engineer-1", tmp_path / "game")
    _, through_second = running(client, backend, "engineer-2", tmp_path / "other")
    assert call(client, through_first, "tools/call").json()["result"] == {"ok": True}
    assert call(client, through_second, "tools/call").json()["result"] == {"ok": True}
    assert reached == ["tools/call", "tools/call"]


def test_a_task_in_a_worktree_of_its_own_drives_its_own_editor_without_waiting(app, tmp_path):
    client, runner, store, backend, reached = app
    first, through_first = running(client, backend, "engineer-1")
    second, through_second = running(client, backend, "engineer-2")
    store.update_task(second, worktree_path=str(tmp_path / "wts" / "game" / second), branch=f"crewbots/{second}")
    runner.worktree_root = tmp_path / "wts"

    assert call(client, through_first, "tools/call").json()["result"] == {"ok": True}
    assert call(client, through_second, "tools/call").json()["result"] == {"ok": True}  # its own Editor, not the first's
    assert reached == ["tools/call", "tools/call"] and [h["task"] for h in runner._apps.values()] == [first]


def told(store, task_id):
    return [e.data["text"] for e in store.events(task_id) if e.kind == "message" and e.data["from"] == "crewbots"]


def test_a_task_in_no_projects_folder_waits_for_any_projects_app_and_holds_them_all(app, tmp_path):
    client, runner, store, backend, reached = app
    game = tmp_path / "game"
    game.mkdir()
    working, through_work = running(client, backend, "engineer-1", game)
    talking, through_talk = running(client, backend, "engineer-2")  # a chat or a group chat's seat: no folder

    assert call(client, through_work, "tools/call").json()["result"] == {"ok": True}
    busy = call(client, through_talk, "tools/call").json()["result"]["content"][0]["text"]
    assert f"UnityMCP is in use for game by engineer-1 ({working})" in busy
    runner.free_apps(ended=working)
    assert told(store, talking) == ["UnityMCP is free now for this project: go on with what needed it."]
    assert call(client, through_talk, "tools/call").json()["result"] == {"ok": True}
    busy = call(client, through_work, "tools/call").json()["result"]["content"][0]["text"]
    assert f"UnityMCP is in use for a project by engineer-2 ({talking})" in busy
    assert reached == ["tools/call", "tools/call"]


def test_a_bot_is_told_when_its_own_task_has_the_app(app, tmp_path):
    client, runner, store, backend, reached = app
    game = tmp_path / "game"
    game.mkdir()
    working, through = running(client, backend, "engineer-1", game)
    seat = client.post("/tasks", json={"bot": "engineer-1", "goal": "talk"},
                       headers={"Authorization": f"Bearer {ADMIN}"}).json()["id"]
    for _ in range(200):
        if seat in runner._runs:
            break
        time.sleep(0.01)
    assert call(client, through, "tools/call").json()["result"] == {"ok": True}
    busy = runner.app_call(seat, "UnityMCP", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call"}).encode())
    assert f"by your own task {working}, which has that work: leave it to that task" in busy["result"]["content"][0]["text"]


def test_a_task_lets_go_of_the_app_before_its_turn_ends(app, tmp_path):
    client, runner, store, backend, reached = app
    game = tmp_path / "game"
    game.mkdir()
    first, through_first = running(client, backend, "engineer-1", game)
    second, through_second = running(client, backend, "engineer-2", game)
    assert call(client, through_first, "tools/call").json()["result"] == {"ok": True}
    assert call(client, through_second, "tools/call").json()["result"]["isError"]

    assert runner.release_app(first).startswith("Let go of UnityMCP for game: the next task waiting for it is told now.")
    assert told(store, second) == ["UnityMCP is free now for game: go on with what needed it."]
    assert call(client, through_second, "tools/call").json()["result"] == {"ok": True}
    assert "in use for game by engineer-2" in call(client, through_first, "tools/call").json()["result"]["content"][0]["text"]
    assert "nothing to let go of" in runner.release_app(first)


def test_only_a_shared_app_of_the_bots_own_is_reached_this_way(app):
    client, runner, store, backend, reached = app
    _, through = running(client, backend, "engineer-1")
    other = McpServer(url=through.url.replace("UnityMCP", "blender"), headers=through.headers, transport="http")
    assert call(client, other, "tools/list").status_code == 403
    assert call(client, McpServer(url=through.url, transport="http"), "tools/list").status_code == 403  # no token
    assert reached == []


def test_a_shared_app_is_an_http_server_and_stays_shared_when_edited(tmp_path):
    with pytest.raises(crew_module.CrewError, match="one_at_a_time only as an http server"):
        crew_module.mcp_server("blender", {"command": "blender-mcp", "one_at_a_time": True}, "mcp.json")
    assert crew_module.mcp_server("UnityMCP", {"url": "http://x/mcp", "one_at_a_time": True}, "mcp.json").one_at_a_time
    mcp_catalog.save(tmp_path, "UnityMCP", {"type": "http", "url": "http://x/mcp", "one_at_a_time": True})
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("chief")), {"fake": HeldBackend()})
    with TestClient(create_app(runner, ADMIN, PORT), base_url=f"http://127.0.0.1:{PORT}/v1") as client:
        client.put("/mcp/servers/UnityMCP", json={"transport": "http", "url": "http://y/mcp"},
                   headers={"Authorization": f"Bearer {ADMIN}"})
    assert mcp_catalog.servers(tmp_path)["UnityMCP"] == {"type": "http", "url": "http://y/mcp", "one_at_a_time": True}
    store.close()
