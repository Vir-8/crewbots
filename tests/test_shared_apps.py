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


def running(client, backend, name):
    """A task of `name`'s, held mid-turn, and how it reaches Unity."""
    task = client.post("/tasks", json={"bot": name, "goal": "go"}, headers={"Authorization": f"Bearer {ADMIN}"}).json()
    for _ in range(200):
        for spec in backend.specs:
            if f"You are the {name}." in spec.system_prompt:
                return task["id"], spec.mcp_servers["UnityMCP"]
        time.sleep(0.01)
    raise TimeoutError(name)


def call(client, server, method, id=1):
    return client.post(server.url.removeprefix(f"http://127.0.0.1:{PORT}/v1"), headers=server.headers,
                       json={"jsonrpc": "2.0", "id": id, "method": method, "params": {}})


def test_one_task_at_a_time_drives_a_shared_app(app):
    client, runner, store, backend, reached = app
    first, through_first = running(client, backend, "engineer-1")
    second, through_second = running(client, backend, "engineer-2")
    assert through_first.url == f"http://127.0.0.1:{PORT}/v1/bot/mcp/UnityMCP" and through_first.url == through_second.url
    assert through_first.headers["Authorization"] != through_second.headers["Authorization"]  # each as its own task

    answer = call(client, through_first, "tools/call")
    assert answer.json()["result"] == {"ok": True} and answer.headers["mcp-session-id"] == "s1"
    busy = call(client, through_second, "tools/call", id=7).json()
    assert busy["id"] == 7 and busy["result"]["isError"]
    assert f"UnityMCP is in use by engineer-1 ({first})" in busy["result"]["content"][0]["text"]
    assert call(client, through_second, "tools/list").json()["result"] == {"ok": True}  # looking never waits
    assert reached == ["tools/call", "tools/list"]

    runner._apps["UnityMCP"]["last"] -= APP_IDLE_S  # the first has left it alone
    runner.free_apps()
    told = [e.data["text"] for e in store.events(second) if e.kind == "message" and e.data["from"] == "crewbots"]
    assert told == ["UnityMCP is free now: go on with what needed it."]
    assert call(client, through_second, "tools/call").json()["result"] == {"ok": True}
    assert "is in use by engineer-2" in call(client, through_first, "tools/call").json()["result"]["content"][0]["text"]


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
