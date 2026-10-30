"""Codex's approval requests answered by crewbots' policy: the same rules Claude bots get, for the same reasons.

A fake app-server plays a turn: it asks for approvals the way Codex does, and records what crewbots answers."""

import asyncio
from pathlib import Path

import pytest

from crewbots.backends import Access, RunSpec
from crewbots.backends.codex_app_server import CodexSession
from crewbots.backends.policy import NO_NETWORK, STAY_IN_SANDBOX, USERS_TO_DO
from crewbots.events import Done, Notice, SessionStarted


class FakeAppServer:
    """Answers crewbots' requests, and sends what a turn sends: items, approval requests, then turn/completed."""

    def __init__(self, turn: list[dict]):
        self.turn = turn
        self.sent: list[dict] = []
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.alive = True

    async def send(self, obj: dict) -> None:
        self.sent.append(obj)
        method = obj.get("method")
        if method == "turn/start":
            await self.inbox.put({"id": obj["id"], "result": {"turn": {"id": "turn-1"}}})
            for message in self.turn:
                await self.inbox.put(message)
        elif method and "id" in obj:
            await self.inbox.put({"id": obj["id"], "result": {"turnId": "turn-1"}})

    async def read(self, timeout_s=None):
        return await asyncio.wait_for(self.inbox.get(), timeout_s or 5)

    def exit_detail(self) -> str:
        return "fake exited"

    async def stop(self) -> None:
        self.alive = False

    def answer_to(self, request_id: str):
        return next(m for m in self.sent if m.get("id") == request_id and "method" not in m)

    def steers(self) -> list[str]:
        return [m["params"]["input"][0]["text"] for m in self.sent if m.get("method") == "turn/steer"]


def command_approval(request_id: str, command: str, **extra) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "th", "turnId": "turn-1", "itemId": f"item-{request_id}", "command": command, **extra}}


def file_change(request_id: str, path: str) -> list[dict]:
    item = {"type": "fileChange", "id": f"item-{request_id}", "changes": [{"path": path, "kind": "add"}], "status": "inProgress"}
    return [
        {"method": "item/started", "params": {"turnId": "turn-1", "item": item}},
        {"jsonrpc": "2.0", "id": request_id, "method": "item/fileChange/requestApproval",
         "params": {"threadId": "th", "turnId": "turn-1", "itemId": item["id"]}},
    ]


COMPLETED = {"method": "turn/completed", "params": {"threadId": "th", "turn": {"id": "turn-1", "status": "completed"}}}


def session_on(server: FakeAppServer, access: Access, cwd: Path) -> CodexSession:
    session = CodexSession(server, RunSpec(prompt="", cwd=cwd, access=access))
    session.session_id = "th"
    session._announced = True
    return session


async def run_turn(session: CodexSession) -> list:
    return [e async for e in session.turn("go")]


async def test_prompt_dispatch_follows_turn_start_not_session_announcement(tmp_path):
    server = FakeAppServer([COMPLETED])
    sent = []
    session = CodexSession(server, RunSpec(prompt="", cwd=tmp_path, on_prompt_sent=lambda: sent.append(True)))
    session.session_id = "th"
    turn = session.turn("go")
    assert isinstance(await anext(turn), SessionStarted)
    assert not sent and not server.sent
    assert isinstance(await anext(turn), Done)
    assert sent == [True] and server.sent[0]["method"] == "turn/start"
    await turn.aclose()


async def test_context_is_measured_against_the_bots_window_not_the_models(tmp_path):
    usage = {"method": "thread/tokenUsage/updated",
             "params": {"tokenUsage": {"last": {"inputTokens": 100_000}, "modelContextWindow": 872_000}}}

    async def percent(context_window):
        session = CodexSession(FakeAppServer([usage, COMPLETED]), RunSpec(prompt="", cwd=tmp_path, context_window=context_window))
        session.session_id = "th"
        assert await session.context_percent() is None  # nothing measured before a turn
        await run_turn(session)
        return await session.context_percent()

    assert await percent(200_000) == 50.0
    assert round(await percent(None), 1) == 11.5  # with no window of its own, the model's 872K


@pytest.fixture
def folder(tmp_path):
    path = tmp_path / "worktree"
    path.mkdir()
    return path


async def test_no_command_leaves_the_sandbox_and_outward_ones_say_why(folder):
    server = FakeAppServer([
        command_approval("a1", "pytest -q"),
        command_approval("a2", "git push origin main"),
        # Codex carries on after a decline; that's when crewbots tells it why.
        {"method": "item/completed",
         "params": {"turnId": "turn-1", "item": {"type": "agentMessage", "id": "m1", "text": "Push declined."}}},
        COMPLETED,
    ])
    session = session_on(server, Access.WORKSPACE_WRITE, folder)

    events = await run_turn(session)

    assert server.answer_to("a1")["result"] == {"decision": "decline"}  # accepting would run it unsandboxed
    assert server.answer_to("a2")["result"] == {"decision": "decline"}
    assert Notice(message=f"Refused pytest -q: {STAY_IN_SANDBOX}") in events
    assert Notice(message=f"Refused git push origin main: {USERS_TO_DO}") in events
    assert any("crewbots declined git push origin main" in s and USERS_TO_DO in s for s in server.steers())
    assert events[-1] == Done(final_text="Push declined.")


async def test_a_read_only_bot_never_leaves_its_sandbox_either(folder):
    server = FakeAppServer([command_approval("a1", "python -m pytest"), command_approval("a2", "git reset --hard"), COMPLETED])
    session = session_on(server, Access.READ_ONLY, folder)

    events = await run_turn(session)

    assert server.answer_to("a1")["result"] == {"decision": "decline"}
    assert Notice(message=f"Refused git reset --hard: {USERS_TO_DO}") in events


async def test_file_changes_are_allowed_only_inside_the_tasks_folder(folder):
    server = FakeAppServer([
        *file_change("f1", str(folder / "src" / "new.py")),
        *file_change("f2", str(folder.parent / "elsewhere.py")),
        COMPLETED,
    ])
    session = session_on(server, Access.WORKSPACE_WRITE, folder)

    events = await run_turn(session)

    assert server.answer_to("f1")["result"] == {"decision": "accept"}
    assert server.answer_to("f2")["result"] == {"decision": "decline"}
    assert any(isinstance(e, Notice) and "own folder" in e.message for e in events)


async def test_a_read_only_bot_never_changes_files(folder):
    server = FakeAppServer([*file_change("f1", str(folder / "a.py")), COMPLETED])
    session = session_on(server, Access.READ_ONLY, folder)

    await run_turn(session)

    assert server.answer_to("f1")["result"] == {"decision": "decline"}


async def test_network_access_outside_the_sandbox_is_declined(folder):
    server = FakeAppServer([
        command_approval("n1", "curl https://example.com", networkApprovalContext={"host": "example.com", "protocol": "https"}),
        COMPLETED,
    ])
    session = session_on(server, Access.WORKSPACE_WRITE, folder)

    events = await run_turn(session)

    assert server.answer_to("n1")["result"] == {"decision": "decline"}
    assert Notice(message=f"Refused network access to example.com: {NO_NETWORK}") in events


async def test_anything_else_codex_asks_is_refused_not_left_hanging(folder):
    server = FakeAppServer([
        {"jsonrpc": "2.0", "id": "q1", "method": "item/tool/requestUserInput", "params": {}},
        COMPLETED,
    ])
    session = session_on(server, Access.WORKSPACE_WRITE, folder)

    await run_turn(session)

    assert "error" in server.answer_to("q1")


def test_full_access_means_no_asking(folder):
    session = CodexSession(FakeAppServer([]), RunSpec(prompt="", cwd=folder, access=Access.FULL))

    assert session.approval_policy == "never" and session.policy is None


def test_every_other_access_level_asks_only_to_leave_the_sandbox(folder):
    for access in (Access.READ_ONLY, Access.WORKSPACE_WRITE):
        assert CodexSession(FakeAppServer([]), RunSpec(prompt="", cwd=folder, access=access)).approval_policy == "on-request"


async def test_refusals_name_the_command_not_codexs_shell_wrapper(folder):
    server = FakeAppServer([command_approval("a1", "/bin/zsh -lc 'git push origin main'"), COMPLETED])
    session = session_on(server, Access.WORKSPACE_WRITE, folder)

    events = await run_turn(session)

    assert Notice(message=f"Refused git push origin main: {USERS_TO_DO}") in events
