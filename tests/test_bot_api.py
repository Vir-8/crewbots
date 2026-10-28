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
    where = {"question": "Where?", "choices": [{"label": "Staging"}, {"label": "Prod", "description": "now"}]}
    client.post("/bot/escalate", json=where, headers=as_bot(token)).raise_for_status()

    assert store.latest_handoff(task_id).output == ["a.py"]
    assert [e.data for e in store.events(task_id) if e.kind == "escalation"] == [
        {"question": "ship?"},
        {"question": "Where?", "choices": [{"label": "Staging"}, {"label": "Prod", "description": "now"}], "multiple": False}]


def test_run_tokens_and_the_users_token_open_different_doors(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "chief")

    assert client.get("/tasks", headers=as_bot(token)).status_code == 403  # a bot can't act as the user
    assert client.post("/push-proposals/1/push", headers=as_bot(token)).status_code == 403
    assert client.get("/bot/tasks", headers=as_bot(ADMIN)).status_code == 403  # nor the user as a task
    assert client.get("/bot/tasks", headers=as_bot("made-up")).status_code == 403
    assert client.get("/bot/tasks", headers={**as_bot(token), "Host": "evil.example:8765"}).status_code == 403


def test_only_user_can_change_automatic_merge_setting(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "chief")
    assert client.get("/crew/merge-settings", headers=as_bot(ADMIN)).json() == {"auto_merge": False}
    assert client.put("/crew/merge-settings", headers=as_bot(token), json={"auto_merge": True}).status_code == 403
    assert client.put("/crew/merge-settings", headers=as_bot(ADMIN), json={"auto_merge": True}).json() == {"auto_merge": True}
    assert client.get("/crew/merge-settings", headers=as_bot(ADMIN)).json() == {"auto_merge": True}


def test_coordinator_setting_and_live_server_authorization(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "engineer")
    headers = as_bot(ADMIN)
    assert client.put("/crew/coordinators", headers=as_bot(token), json={"coordinators": ["engineer"]}).status_code == 403
    assert client.put("/crew/coordinators", headers=headers, json={"coordinators": ["missing"]}).status_code == 422
    assert client.put("/crew/coordinators", headers=headers, json={"coordinators": ["engineer", "engineer"]}).status_code == 422
    assert client.put("/crew/coordinators", headers=headers, json={"coordinators": ["engineer"]}).json() == {
        "coordinators": ["engineer"]}
    assert client.post("/bot/next_check", headers=as_bot(token), json={"minutes": 10, "why": "work"}).status_code == 200
    assert client.post("/bot/add_bot", headers=as_bot(token), json={"name": "new", "title": "New",
        "description": "New", "instructions": "New"}).status_code == 403
    assert client.post("/bot/propose_push", headers=as_bot(token),
                       json={"project": "demo", "summary": "Ready"}).status_code == 403
    assert client.put("/crew/coordinators", headers=headers, json={"coordinators": []}).status_code == 200
    assert client.post("/bot/next_check", headers=as_bot(token), json={"minutes": 10, "why": "work"}).status_code == 403


def test_chief_setting_is_user_only_and_immediately_revokes_old_chief(crew_app):
    client, backend, store = crew_app
    chief_id, token = start(client, backend, "chief")
    store.set_coordinators([])
    work = store.create_task("engineer", "work", parent_id=chief_id, created_by="chief")
    store.update_task(work.id, merge_state="proposed")
    assert client.put("/crew/chief", headers=as_bot(token), json={"chief": "engineer"}).status_code == 403
    assert client.put("/crew/chief", headers=as_bot(ADMIN), json={"chief": "missing"}).status_code == 422
    assert client.put("/crew/chief", headers=as_bot(ADMIN), json={"chief": "engineer"}).json() == {
        "chief": "engineer", "available": True}
    refused = client.post("/bot/withdraw_merge", headers=as_bot(token), json={"task_id": work.id, "reason": "old"})
    assert refused.status_code == 403
    engineer_id, engineer_token = start(client, backend, "engineer")
    new_work = store.create_task("chief", "work", parent_id=engineer_id, created_by="engineer")
    store.update_task(new_work.id, merge_state="proposed")
    result = client.post("/bot/withdraw_merge", headers=as_bot(engineer_token),
                         json={"task_id": new_work.id, "reason": "old"})
    assert result.status_code == 200


def test_missing_chief_disables_existing_chief_session(crew_app):
    client, backend, store = crew_app
    chief_id, token = start(client, backend, "chief")
    store.set_coordinators([])
    work = store.create_task("engineer", "work", parent_id=chief_id, created_by="chief")
    store.update_task(work.id, merge_state="proposed")
    store.set_chief("gone")
    assert client.get("/crew/chief", headers=as_bot(ADMIN)).json() == {"chief": "gone", "available": False}
    assert client.get("/chats", headers=as_bot(ADMIN)).status_code == 200
    result = client.post("/bot/withdraw_merge", headers=as_bot(token), json={"task_id": work.id, "reason": "old"})
    assert result.status_code == 403
    assert store.get_task(work.id).merge_state == "proposed"


def test_worker_cannot_call_propose_merge_directly(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "engineer")
    response = client.post("/bot/propose_merge", headers=as_bot(token), json={"task_id": "t_any", "summary": "work"})
    assert response.status_code == 403
    assert "only a coordinator" in response.json()["detail"]
    response = client.post("/bot/withdraw_merge", headers=as_bot(token), json={"task_id": "t_any", "reason": "old"})
    assert response.status_code == 403
    assert "only a coordinator" in response.json()["detail"]


def test_coordinator_and_user_can_withdraw_proposals(crew_app):
    client, backend, store = crew_app
    chief_id, token = start(client, backend, "chief")
    work = store.create_task("engineer", "old work", parent_id=chief_id, created_by="chief")
    store.update_task(work.id, merge_state="proposed")
    other = store.create_task("engineer", "other work", created_by="chief")
    store.update_task(other.id, merge_state="proposed")
    refused = client.post("/bot/withdraw_merge", headers=as_bot(token), json={"task_id": other.id, "reason": "old"})
    assert refused.status_code == 403 and store.get_task(other.id).merge_state == "proposed"
    assert not store.events(other.id)
    result = client.post("/bot/withdraw_merge", headers=as_bot(token), json={"task_id": work.id, "reason": "Superseded"})
    assert result.status_code == 200 and store.get_task(work.id).merge_state is None
    assert store.events(work.id)[-1].data == {"by": "chief", "reason": "Superseded"}
    store.update_task(work.id, merge_state="proposed")
    result = client.post(f"/tasks/{work.id}/withdraw-merge", headers=as_bot(ADMIN), json={"reason": "Dismissed"})
    assert result.status_code == 200 and result.json()["merge_state"] is None
    assert store.events(work.id)[-1].data == {"by": "user", "reason": "Dismissed"}


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

    asked = await server.call_tool("escalate", {"question": "Which?", "choices": [{"label": "A", "description": "the first"}]})
    assert "the answer arrives as your next message." in str(asked)
    assert store.events(chief_id)[-1].data == {"question": "Which?", "choices": [{"label": "A", "description": "the first"}],
                                               "multiple": False}


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
    chief = {t.name for t in await build_server(client, can_delegate=True, is_chief=True).list_tools()}

    assert worker == {"handoff", "escalate", "review", "remember", "forget", "recall", "start_thread", "post",
                      "delegate", "tasks", "message", "stop_task", "ask", "open_group", "add_to_group", "working_state",
                      "update_state", "remind_on",
                      "propose_routine", "share", "write_page", "edit_page", "set_name",
                      "ask_permission", "browser_open", "browser_screenshot", "browser_click",
                      "browser_type", "browser_evaluate", "browser_console"}
    assert coordinator == worker | {"propose_merge", "withdraw_merge", "drop_task", "next_check", "room_status",
                                    "sweep_worktrees"}
    assert chief == coordinator | {"add_bot", "propose_push", "propose_replace_main"}


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
