import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from fakes import FakeBackend, bot, make_crew, wait_until
from mcp.server.mcpserver.exceptions import ToolError

from crewbots.backends import antigravity_runtime as runtime
from crewbots.backends.antigravity import WORKSPACE, Antigravity, AntigravitySession, mcp_config
from crewbots.backends.antigravity_tools import Workspace
from crewbots.backends.base import McpServer, RunSpec
from crewbots.events import Done, Text, ToolCall, ToolResult, Usage
from crewbots.runner import Runner
from crewbots.store import Status, Store
from crewbots.usage import summary


class Connection:
    def __init__(self, result=None):
        self.updates = asyncio.Queue()
        self.process = type("Process", (), {"alive": True})()
        self.calls = []
        self.closed = False
        self.result = result or {"stopReason": "end_turn"}

    async def request(self, method, params, timeout=30):
        self.calls.append((method, params))
        return self.result

    async def notify(self, method, params):
        self.calls.append((method, params))

    async def close(self):
        self.closed = True


def session(tmp_path, connection=None, **kwargs):
    found = AntigravitySession(connection or Connection(), RunSpec(prompt="", cwd=tmp_path, **kwargs))
    found.session_id = "test-session"
    return found


def update(value):
    return {"sessionId": "test-session", "update": value}


def test_only_advertised_models_and_thinking_levels_are_offered():
    found = runtime.model_list({"configOptions": [
        {"id": "model", "currentValue": "offered-model", "options": [{"value": "offered-model", "name": "Offered"}]},
        {"id": "thinking_level", "currentValue": "medium", "options": [{"value": "low"}, {"value": "medium"}]},
    ]})
    assert found == [{"id": "offered-model", "label": "Offered", "description": "", "efforts": ["low", "medium"],
                      "default_effort": "medium", "default": True}]
    assert runtime.model_list({}) == []


def test_mcp_servers_keep_headers_env_and_transport():
    found = mcp_config({"local": McpServer(command="test", args=["one"], env={"KEY": "value"}),
                        "remote": McpServer(transport="http", url="http://localhost/mcp", headers={"X-Test": "value"})})
    assert found == [
        {"name": "local", "command": "test", "args": ["one"], "env": [{"name": "KEY", "value": "value"}]},
        {"name": "remote", "type": "http", "url": "http://localhost/mcp", "headers": [{"name": "X-Test", "value": "value"}]},
    ]


@pytest.mark.parametrize("server,call_id,allowed", [(WORKSPACE, "call", True), ("granted", "call", True),
                                                   ("rogue", "call", False), (WORKSPACE, "interaction_1", False)])
async def test_only_granted_mcp_calls_get_one_time_approval(tmp_path, server, call_id, allowed):
    opened = session(tmp_path, mcp_servers={"granted": McpServer(command="test")})
    try:
        result = await opened._permission("session/request_permission", {
            "sessionId": opened.session_id,
            "toolCall": {"toolCallId": call_id, "_meta": {"mcp": {"server": server, "tool": "read"}}},
            "options": [{"kind": "allow_always", "optionId": "always"},
                        {"kind": "allow_once", "optionId": "allow"}, {"kind": "reject_once", "optionId": "deny"}],
        })
        assert result["outcome"]["optionId"] == ("allow" if allowed else "deny")
        with pytest.raises(ValueError):
            await opened._permission("fs/write_text_file", {"path": "/tmp/not-allowed"})
    finally:
        await opened.close()


async def test_tool_results_and_images_are_preserved_without_duplicate_calls(tmp_path):
    opened = session(tmp_path)
    try:
        call = {"sessionUpdate": "tool_call", "toolCallId": "one", "status": "pending", "rawInput": {"x": 1},
                "_meta": {"mcp": {"server": "unity", "tool": "screenshot"}}}
        assert opened.events(update(call)) == [ToolCall(id="one", name="mcp__unity__screenshot", input={"x": 1})]
        assert opened.events(update({**call, "sessionUpdate": "tool_call_update", "status": "in_progress"})) == []
        done = {"sessionUpdate": "tool_call_update", "toolCallId": "one", "status": "completed", "content": [
            {"type": "content", "content": {"type": "text", "text": "actual output"}},
            {"type": "content", "content": {"type": "image", "mimeType": "image/png", "data": "png"}},
        ]}
        assert opened.events(update(done)) == [ToolResult(id="one", output="actual output", images=[
            {"mime": "image/png", "data": "png"}])]
        assert opened.events(update(done)) == []
        assert opened.events({"sessionId": "someone-else", "update": call}) == []
    finally:
        await opened.close()


async def test_usage_is_per_turn_and_includes_cached_and_thinking_tokens(tmp_path):
    connection = Connection({"stopReason": "end_turn", "usage": {
        "inputTokens": 100, "cachedReadTokens": 900, "outputTokens": 20, "thoughtTokens": 30}})
    opened = session(tmp_path, connection)
    try:
        for _ in range(2):
            await connection.updates.put(update({"sessionUpdate": "agent_message_chunk",
                                                  "content": {"type": "text", "text": "hello"}}))
            events = [event async for event in opened.turn("say hello")]
            assert [e for e in events if isinstance(e, Usage)] == [
                Usage(input_tokens=1000, cached_input_tokens=900, output_tokens=50)]
            assert Text("hello") in events
            assert events[-1] == Done("hello")
        assert len(connection.calls) == 2
    finally:
        await opened.close()


async def test_missing_tool_result_is_unverified_even_when_turn_succeeds(tmp_path):
    connection = Connection()
    opened = session(tmp_path, connection)
    await connection.updates.put(update({"sessionUpdate": "tool_call", "toolCallId": "one", "status": "in_progress"}))
    try:
        events = [e async for e in opened.turn("one task")]
        result = next(e for e in events if isinstance(e, ToolResult))
        assert result.is_error and "unverified" in result.output
    finally:
        await opened.close()


async def test_timeout_cancels_without_retrying(tmp_path):
    connection = Connection()

    async def hang(method, params, timeout=30):
        await asyncio.sleep(timeout)
        raise TimeoutError()

    connection.request = hang
    opened = session(tmp_path, connection, timeout_s=0.01)
    events = [e async for e in opened.turn("one task")]
    assert events[-1].is_error and events[-1].retry is None
    assert connection.closed
    assert connection.calls == [("session/cancel", {"sessionId": "test-session"})]


async def test_session_setup_disables_native_tools_and_keeps_requested_model(tmp_path):
    connection = Connection({"sessionId": "new", "configOptions": [{"id": "model", "currentValue": "offered",
        "options": [{"value": "offered", "name": "Offered"}]}]})
    opened = session(tmp_path, connection, model="not-offered")
    try:
        with pytest.raises(ValueError, match="doesn't offer"):
            await opened.start()
        params = connection.calls[0][1]
        assert params["_meta"]["antigravity"]["enabledTools"] == ["finish"]
        assert all(method != "session/prompt" for method, _ in connection.calls)
        assert params["mcpServers"][0]["name"] == WORKSPACE
    finally:
        await opened.close()


async def test_proactive_runs_are_rejected_before_connecting(tmp_path):
    with pytest.raises(ValueError, match="proactive"):
        await Antigravity().open(RunSpec(prompt="", cwd=tmp_path, proactive=True))


def test_subscription_environment_drops_ambient_api_credentials(monkeypatch):
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "AGY_LLM_GATEWAY_URL",
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(key, "do-not-use")
    env = runtime.environment()
    assert "do-not-use" not in env.values()
    assert env["GEMINI_HOME"] == str(runtime.profile())
    assert "ANTIGRAVITY_HARNESS_PATH" in env


def workspace(tmp_path, access="workspace_write"):
    cwd, config = tmp_path / "workspace", tmp_path / "config"
    cwd.mkdir(exist_ok=True)
    config.mkdir(exist_ok=True)
    return Workspace({"cwd": str(cwd), "temp": str(config), "access": access, "network": False,
                      "write_dirs": [], "deny_read": [str(tmp_path / "other-bot-state.md")]})


def test_workspace_refuses_private_files_and_writes_outside_grants(tmp_path):
    w = workspace(tmp_path)
    secret = Path.home() / ".gemini" / "private"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret")
    (w.cwd / "link").symlink_to(secret)
    for path in (str(secret), "link", str(w.temp / "tools.json")):
        with pytest.raises(ToolError):
            w.read(path)
    with pytest.raises(ToolError):
        w.write("../outside.txt")
    assert w.write("good.txt") == w.cwd / "good.txt"
    with pytest.raises(ToolError):
        workspace(tmp_path, "read_only").write("good.txt")


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="macOS sandbox")
async def test_commands_obey_write_read_and_network_boundaries(tmp_path):
    w = workspace(tmp_path)
    secret = Path.home() / ".gemini" / "private"
    secret.parent.mkdir(parents=True)
    secret.write_text("PRIVATE_VALUE")
    assert "Exit code: 0" in await w.command("echo ok > allowed.txt")
    assert (w.cwd / "allowed.txt").read_text().strip() == "ok"
    assert "Exit code: 0" not in await w.command("echo bad > ../outside.txt")
    assert not (tmp_path / "outside.txt").exists()
    assert "PRIVATE_VALUE" not in await w.command(f"cat {secret}")
    assert "Exit code: 0" not in await workspace(tmp_path, "read_only").command("echo bad > blocked.txt")
    assert not (w.cwd / "blocked.txt").exists()
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        command = f"python3 -c 'import socket; socket.create_connection((\"127.0.0.1\", {port}), 1); print(\"CONNECTED\")'"
        assert "CONNECTED" not in await w.command(command)
        w.policy.network = True
        assert "CONNECTED" in await w.command(command)
    finally:
        server.close()
        await server.wait_closed()


async def test_runner_does_not_retry_or_resume_antigravity_automatically(tmp_path):
    backend = FakeBackend(reply=lambda spec: [Done("", is_error=True, error="quota", retry="transient")])
    backend.automatic_turns = False
    store = Store(":memory:")
    runner = Runner(store, make_crew(tmp_path, replace(bot(), backend="antigravity")), {"antigravity": backend})
    try:
        task = runner.submit("worker", "requested work")
        await wait_until(lambda: store.get_task(task.id).status is Status.FAILED)
        assert len(backend.specs) == 1
        assert not runner.capacity.verdict("antigravity").go
        runner.send(task.id, "Background activity notice", sender="crewbots")
        assert task.id not in runner.running()
        assert len(backend.specs) == 1
        store.set_pending(task.id, "do not auto-resume")
        store.update_task(task.id, status=Status.INTERRUPTED)
        assert runner.resume_interrupted() == []
        store.append_event(task.id, kind="usage", data={"input_tokens": 100, "cached_input_tokens": 60,
                                                      "output_tokens": 10, "plan": "antigravity"})
        usage = summary(store, runner.bot_for)
        assert usage["totals"]["antigravity"] == 50
        assert usage["totals"]["claude"] == 0
    finally:
        await runner.shutdown()
        store.close()
