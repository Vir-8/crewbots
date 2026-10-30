"""The /bot/ API and the MCP server on top of it: what a bot's tools can and cannot do."""

import json
import re
import time
from pathlib import Path

import pytest
from fakes import ADMIN, as_bot, start

from crewbots.crew_tools import SPECS, tools_for
from crewbots.mcp_server import build_server


def test_a_coordinator_delegates_and_sees_its_tasks(crew_app):
    client, backend, store = crew_app
    chief_id, token = start(client, backend, "chief")

    created = client.post("/bot/tools/delegate", json={"bot": "engineer", "goal": "build it"}, headers=as_bot(token))

    assert created.status_code == 200
    [child] = store.children(chief_id)
    assert created.json()["content"].startswith(f"Delegated {child.id} to engineer")
    assert (child.created_by, child.bot) == ("chief", "engineer")
    listed = json.loads(client.post("/bot/tools/tasks", json={}, headers=as_bot(token)).json()["content"])
    assert [t["id"] for t in listed] == [child.id]


def test_the_app_finds_the_child_in_each_reply_that_makes_one(crew_app):
    client, backend, store = crew_app
    chief_id, token = start(client, backend, "chief")
    source = (Path(__file__).parents[1] / "app/src/components/threadBlocks.ts").read_text()
    work_match, ask_match = re.findall(r"\.match\(/(.+?)/\)", source)  # how the app anchors work and Asked cards

    replies = [(work_match, client.post("/bot/tools/delegate", json={"bot": "engineer", "goal": "build it"},
                                        headers=as_bot(token))),
               (work_match, client.post("/bot/tools/start_thread", json={"title": "Spike", "goal": "try it"},
                                        headers=as_bot(token))),
               (ask_match, client.post("/bot/tools/ask", json={"bot": "engineer", "question": "which?"},
                                       headers=as_bot(token)))]

    found = [re.search(pattern, reply.json()["content"])[1] for pattern, reply in replies]
    assert sorted(found) == sorted(child.id for child in store.children(chief_id))


def test_a_refused_delegation_says_why_and_is_logged(crew_app):
    client, backend, store = crew_app
    _, token = start(client, backend, "engineer")

    refused = client.post("/bot/tools/delegate", json={"bot": "engineer", "goal": "x"}, headers=as_bot(token))

    assert refused.status_code == 422
    assert "that's you" in refused.json()["detail"]
    logged = store.agent_log()[0]
    assert (logged["kind"], logged["bot"]) == ("refused", "engineer") and logged["text"].startswith("delegate: that's you")


def test_handoffs_and_escalations_are_recorded_on_the_callers_task(crew_app):
    client, backend, store = crew_app
    task_id, token = start(client, backend, "engineer")

    client.post("/bot/tools/handoff", json={"done": "built", "output": ["a.py"]}, headers=as_bot(token)).raise_for_status()
    client.post("/bot/tools/escalate", json={"question": "ship?"}, headers=as_bot(token)).raise_for_status()
    where = {"question": "Where?", "choices": [{"label": "Staging"}, {"label": "Prod", "description": "now"}]}
    client.post("/bot/tools/escalate", json=where, headers=as_bot(token)).raise_for_status()

    assert store.latest_handoff(task_id).output == ["a.py"]
    assert [e.data for e in store.events(task_id) if e.kind == "escalation"] == [
        {"question": "ship?"},
        {"question": "Where?", "choices": [{"label": "Staging"}, {"label": "Prod", "description": "now"}], "multiple": False}]


def test_a_correction_is_remembered_and_counted_on_the_callers_task(crew_app):
    client, backend, store = crew_app
    task_id, token = start(client, backend, "engineer")
    lesson = {"title": "Run the WebKit check", "body": "Scroll bugs live there.", "mistake": "Checked Chromium only"}

    def remember(sent):
        return client.post("/bot/tools/remember", json=sent, headers=as_bot(token)).json()["content"]

    first, second, plain = remember(lesson), remember({**lesson, "title": "Again"}), remember({"title": "Note", "body": "b"})

    assert "Counted as a correction" in first and "earlier ones" not in first
    assert second.endswith("Your earlier ones, the latest first: Run the WebKit check.")
    assert "Counted as a correction" not in plain
    [noted, _] = [e.data for e in store.events(task_id) if e.kind == "correction"]
    assert noted == {"bot": "engineer", "title": "Run the WebKit check", "mistake": "Checked Chromium only",
                     "lesson": "Scroll bugs live there.", "check": ""}


def test_run_tokens_and_the_users_token_open_different_doors(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "chief")

    assert client.get("/tasks", headers=as_bot(token)).status_code == 403  # a bot can't act as the user
    assert client.post("/push-proposals/1/push", headers=as_bot(token)).status_code == 403
    for refused in (as_bot(ADMIN), as_bot("made-up"), {**as_bot(token), "Host": "evil.example:8765"}):
        assert client.post("/bot/tools/tasks", json={}, headers=refused).status_code == 403  # the user isn't a task
    assert client.post("/bot/tools/tasks", json={}, headers=as_bot(token)).status_code == 200


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
    check = {"minutes": 10, "why": "work"}
    assert client.post("/bot/tools/next_check", headers=as_bot(token), json=check).status_code == 200
    assert client.post("/bot/tools/add_bot", headers=as_bot(token), json={"name": "new", "title": "New",
        "description": "New", "instructions": "New"}).status_code == 422
    assert client.post("/bot/tools/propose_push", headers=as_bot(token),
                       json={"project": "demo", "summary": "Ready"}).status_code == 422
    assert client.put("/crew/coordinators", headers=headers, json={"coordinators": []}).status_code == 200
    assert client.post("/bot/tools/next_check", headers=as_bot(token), json=check).status_code == 422


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
    refused = client.post("/bot/tools/withdraw_merge", headers=as_bot(token), json={"task_id": work.id, "reason": "old"})
    assert refused.status_code == 422
    engineer_id, engineer_token = start(client, backend, "engineer")
    new_work = store.create_task("chief", "work", parent_id=engineer_id, created_by="engineer")
    store.update_task(new_work.id, merge_state="proposed")
    result = client.post("/bot/tools/withdraw_merge", headers=as_bot(engineer_token),
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
    result = client.post("/bot/tools/withdraw_merge", headers=as_bot(token), json={"task_id": work.id, "reason": "old"})
    assert result.status_code == 422
    assert store.get_task(work.id).merge_state == "proposed"


def test_worker_cannot_call_propose_merge_directly(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "engineer")
    for name, sent in (("propose_merge", {"task_id": "t_any", "summary": "work"}),
                       ("withdraw_merge", {"task_id": "t_any", "reason": "old"})):
        response = client.post(f"/bot/tools/{name}", headers=as_bot(token), json=sent)
        assert response.status_code == 422 and "not one of this bot's crew tools" in response.json()["detail"]


def test_a_run_token_stops_working_when_its_run_ends(crew_app):
    client, backend, store = crew_app
    task_id, token = start(client, backend, "engineer")
    backend.release.set()
    for _ in range(200):
        if not store.get_task(task_id).active:
            break
        time.sleep(0.01)

    assert client.post("/bot/tools/tasks", json={}, headers=as_bot(token)).status_code == 403


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

    await server.call_tool("handoff", {"done": "Planned it", "output": "plan.md", "evidence": "read the brief\nchecked it"})
    handed = store.latest_handoff(chief_id)  # one item sent bare, or a line each, is still a list
    assert handed.output == ["plan.md"] and handed.evidence == ["read the brief", "checked it"]
    await server.call_tool("write_page", {"page": "Scratchpad", "content": "- The plan"})


async def test_what_a_codex_bot_sends_reaches_the_daemon_as_it_sent_it(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "chief")
    client.headers["Authorization"] = f"Bearer {token}"
    server = build_server(client, can_delegate=True)

    added = str(await server.call_tool("working_state", {"section": "Decisions", "action": "add", "text": "Ship Friday"}))
    entry_id = re.search(r"entry (\w+) added", added)[1]
    removed = await server.call_tool("working_state", {"action": "remove", "id": entry_id})  # `id` for entry_id

    assert f"entry {entry_id} removed" in str(removed)
    wrote = str(await server.call_tool("write_page", {"page": "Scratchpad", "text": "- The plan", "colour": "red"}))
    assert "Wrote Scratchpad" in wrote and "takes no `colour`" in wrote  # `text` for content, and told of the rest


def test_a_coordinators_tools_are_refused_to_a_worker_on_the_one_route(crew_app):
    client, backend, _ = crew_app
    _, chief = start(client, backend, "chief")
    _, engineer = start(client, backend, "engineer")
    check = {"minutes": 10, "why": "work"}

    allowed = client.post("/bot/tools/next_check", json=check, headers=as_bot(chief))
    refused = client.post("/bot/tools/next_check", json=check, headers=as_bot(engineer))

    assert allowed.status_code == 200 and "look around again" in allowed.json()["content"]
    assert refused.status_code == 422 and "not one of this bot's crew tools" in refused.json()["detail"]


async def test_a_refusal_reaches_the_bot_as_a_tool_error_it_can_read(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "chief")
    client.headers["Authorization"] = f"Bearer {token}"
    server = build_server(client, can_delegate=True)

    with pytest.raises(Exception, match="no bot named 'ghost'"):
        await server.call_tool("delegate", {"bot": "ghost", "goal": "x"})
    await server.call_tool("write_page", {"page": "Scratchpad", "content": "- The plan"})
    with pytest.raises(Exception, match="not found in Scratchpad"):
        await server.call_tool("edit_page", {"page": "Scratchpad", "find": "no such line", "replace": "x"})


async def test_workers_delegate_but_only_coordinators_add_bots_and_propose_merges(crew_app):
    client, _, _ = crew_app

    worker = {t.name for t in await build_server(client, can_delegate=False).list_tools()}
    coordinator = {t.name for t in await build_server(client, can_delegate=True).list_tools()}
    chief = {t.name for t in await build_server(client, can_delegate=True, is_chief=True).list_tools()}

    assert worker == {t.name for t in tools_for(False)} and "delegate" in worker
    assert coordinator == worker | {"propose_merge", "withdraw_merge", "drop_task", "next_check", "room_status",
                                    "sweep_worktrees"}
    assert chief == coordinator | {"add_bot", "propose_push", "propose_replace_main", "tell_user"}


async def test_codex_is_shown_the_fields_claude_is(crew_app):
    client, _, _ = crew_app
    for listed in await build_server(client, can_delegate=True, is_chief=True).list_tools():
        assert set(listed.input_schema["properties"]) == set(SPECS[listed.name].schema["properties"]), listed.name


async def test_each_task_is_offered_the_tools_for_what_it_is(crew_app):
    client, _, _ = crew_app

    async def offered(**where):
        listed = {t.name for t in await build_server(client, can_delegate=False, **where).list_tools()}
        assert listed == {t.name for t in tools_for(False, **where)}  # the same on Codex as on Claude
        return listed

    chat, seat, thread = await offered(kind="chat"), await offered(kind="seat"), await offered(kind="thread")
    review = await offered(kind="thread", reviewing=True)
    assert "handoff" in thread and not {"handoff", "review"} & (chat | seat)
    assert "set_name" in chat and "set_name" not in seat | thread
    assert {"propose_routine", "remind_on"} <= chat & seat and not {"propose_routine", "remind_on"} & (thread | review)
    assert "review" in review and "review" not in thread
    assert not {"handoff", "delegate", "start_thread", "open_group", "write_page"} & review
    assert {"recall", "remember", "escalate", "share", "browser_open", "webkit_check"} <= chat & seat & thread & review


def test_a_verdict_from_a_task_that_is_not_a_review_is_refused_with_a_reason(crew_app):
    client, backend, _ = crew_app
    _, token = start(client, backend, "engineer")

    refused = client.post("/bot/tools/review", json={"verdict": "pass", "summary": "fine"}, headers=as_bot(token))

    assert refused.status_code == 422 and "not a review" in refused.json()["detail"]
    bad = client.post("/bot/tools/review", json={"verdict": "maybe", "summary": "?"}, headers=as_bot(token))
    assert bad.status_code == 422 and "'pass' or 'fail'" in bad.json()["detail"]


def test_merging_a_task_with_nothing_to_merge_is_a_conflict_the_user_can_read(crew_app):
    client, backend, _ = crew_app
    task_id, _ = start(client, backend, "engineer")
    admin = {"Authorization": f"Bearer {ADMIN}"}

    assert client.get(f"/tasks/{task_id}/review", headers=admin).json()["round"] == 0
    refused = client.post(f"/tasks/{task_id}/merge", headers=admin)
    assert refused.status_code == 409 and "no worktree" in refused.json()["detail"]
