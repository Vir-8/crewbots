"""The /bot/ API and the MCP server on top of it: what a bot's tools can and cannot do."""

import asyncio
import threading
import time

import pytest
from fakes import bot, make_crew
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.events import Done
from crewbots.mcp_server import build_server
from crewbots.runner import MCP_SERVER_NAME, Runner
from crewbots.store import Store

ADMIN = "admin-token"
PORT = 8765


class HeldBackend:
    """Every run stays open until `release` is set, so its run token stays valid while the test uses it."""

    name = "fake"

    def __init__(self):
        self.release = threading.Event()  # the app runs on the test client's own thread and loop
        self.specs = []

    async def run(self, spec):
        self.specs.append(spec)
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        yield Done(final_text="ok")


@pytest.fixture
def crew_app(tmp_path):
    store = Store(tmp_path / "crew.db")
    backend = HeldBackend()
    crew = make_crew(tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    runner = Runner(store, crew, {"fake": backend}, api_url=f"http://127.0.0.1:{PORT}/v1")
    with TestClient(create_app(runner, ADMIN, PORT), base_url=f"http://127.0.0.1:{PORT}/v1") as client:
        yield client, backend, store
        backend.release.set()
    store.close()


def start(client, backend, bot_name):
    """Starts a task for `bot_name` and returns (task id, its run token)."""
    task = client.post("/tasks", json={"bot": bot_name, "goal": "go"}, headers={"Authorization": f"Bearer {ADMIN}"}).json()
    for _ in range(200):
        for spec in backend.specs:
            if f"You are the {bot_name}." in spec.system_prompt:
                return task["id"], spec.mcp_servers[MCP_SERVER_NAME].env["CREWBOTS_TOKEN"]
        time.sleep(0.01)
    raise TimeoutError(bot_name)


def as_bot(token):
    return {"Authorization": f"Bearer {token}"}


def test_a_coordinator_delegates_and_sees_its_tasks(crew_app):
    client, backend, store = crew_app
    chief_id, token = start(client, backend, "chief")

    created = client.post("/bot/delegate", json={"bot": "engineer", "goal": "build it"}, headers=as_bot(token))

    assert created.status_code == 201
    child = store.get_task(created.json()["id"])
    assert (child.parent_id, child.created_by, child.bot) == (chief_id, "chief", "engineer")
    listed = client.get("/bot/tasks", headers=as_bot(token)).json()
    assert [t["id"] for t in listed] == [child.id]


def test_a_refused_delegation_says_why_and_is_logged(crew_app):
    client, backend, store = crew_app
    _, token = start(client, backend, "engineer")

    refused = client.post("/bot/delegate", json={"bot": "engineer", "goal": "x"}, headers=as_bot(token))

    assert refused.status_code == 403
    assert "that's you" in refused.json()["detail"]
    logged = store.agent_log()[0]
    assert (logged["kind"], logged["bot"]) == ("refused", "engineer") and logged["text"].startswith("delegate: that's you")


def test_handoffs_and_escalations_are_recorded_on_the_callers_task(crew_app):
    client, backend, store = crew_app
    task_id, token = start(client, backend, "engineer")

    client.post("/bot/handoff", json={"done": "built", "output": ["a.py"]}, headers=as_bot(token)).raise_for_status()
    client.post("/bot/escalate", json={"question": "ship?"}, headers=as_bot(token)).raise_for_status()

    assert store.latest_handoff(task_id).output == ["a.py"]
    assert "escalation" in [e.kind for e in store.events(task_id)]


def test_run_tokens_and_the_users_token_open_different_doors(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "chief")

    assert client.get("/tasks", headers=as_bot(token)).status_code == 403  # a bot can't act as the user
    assert client.get("/bot/tasks", headers=as_bot(ADMIN)).status_code == 403  # nor the user as a task
    assert client.get("/bot/tasks", headers=as_bot("made-up")).status_code == 403
    assert client.get("/bot/tasks", headers={**as_bot(token), "Host": "evil.example:8765"}).status_code == 403


def test_a_run_token_stops_working_when_its_run_ends(crew_app):
    client, backend, store = crew_app
    task_id, token = start(client, backend, "engineer")
    backend.release.set()
    for _ in range(200):
        if not store.get_task(task_id).active:
            break
        time.sleep(0.01)

    assert client.get("/bot/tasks", headers=as_bot(token)).status_code == 403


async def test_the_mcp_tools_call_the_daemon_as_the_task(crew_app):
    client, backend, store = crew_app
    chief_id, token = start(client, backend, "chief")
    client.headers["Authorization"] = f"Bearer {token}"
    server = build_server(client, can_delegate=True)

    result = await server.call_tool("delegate", {"bot": "engineer", "goal": "build it"})

    assert "Delegated t_" in str(result)
    assert [c.bot for c in store.children(chief_id)] == ["engineer"]


async def test_a_refusal_reaches_the_bot_as_a_tool_error_it_can_read(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "chief")
    client.headers["Authorization"] = f"Bearer {token}"
    server = build_server(client, can_delegate=True)

    with pytest.raises(Exception, match="no bot named 'ghost'"):
        await server.call_tool("delegate", {"bot": "ghost", "goal": "x"})


async def test_workers_delegate_but_only_coordinators_add_bots_and_propose_merges(crew_app):
    client, _, _ = crew_app

    worker = {t.name for t in await build_server(client, can_delegate=False).list_tools()}
    coordinator = {t.name for t in await build_server(client, can_delegate=True).list_tools()}

    assert worker == {"handoff", "escalate", "review", "remember", "forget", "recall", "start_thread", "post",
                      "delegate", "tasks", "message", "ask", "open_group", "add_to_group", "update_state", "remind_on"}
    assert coordinator == worker | {"add_bot", "propose_merge"}


def test_a_verdict_from_a_task_that_is_not_a_review_is_refused_with_a_reason(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "engineer")

    refused = client.post("/bot/review", json={"verdict": "pass", "summary": "fine"}, headers=as_bot(token))

    assert refused.status_code == 403 and "not a review" in refused.json()["detail"]
    bad = client.post("/bot/review", json={"verdict": "maybe", "summary": "?"}, headers=as_bot(token))
    assert bad.status_code == 422  # only pass or fail


def test_merging_unreviewed_work_is_a_conflict_the_user_can_read(crew_app):
    client, backend, _ = crew_app
    task_id, _ = start(client, backend, "engineer")
    admin = {"Authorization": f"Bearer {ADMIN}"}

    assert client.get(f"/tasks/{task_id}/review", headers=admin).json()["round"] == 0
    refused = client.post(f"/tasks/{task_id}/merge", headers=admin)
    assert refused.status_code == 409 and "no worktree" in refused.json()["detail"]
