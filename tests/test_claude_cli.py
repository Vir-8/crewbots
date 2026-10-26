import json

from helpers import collect, fake_command, kinds, lines, write_fixture

from crewbots.backends import Access, ClaudeCLI, McpServer, RunSpec
from crewbots.backends.claude_cli import ClaudeParser
from crewbots.events import Done, RateLimit, SessionStarted, Text, ToolCall, ToolResult, Usage


def parse(objs: list[dict]):
    parser = ClaudeParser()
    return [event for obj in objs for event in parser.feed(obj)]


def test_a_real_run_becomes_the_common_events():
    events = parse(lines("claude_read.jsonl"))

    assert kinds(events) == [
        "session_started", "tool_call", "tool_result", "rate_limit", "tool_call", "tool_result", "text", "usage", "done",
    ]
    assert events[0] == SessionStarted(session_id="2510de92-aaa0-4873-aaca-8540fec35076")
    assert events[3] == RateLimit(plan="claude", window="five_hour", status="ok", resets_at=1792964400)
    events = [e for e in events if not isinstance(e, RateLimit)]
    read = events[3]
    assert isinstance(read, ToolCall) and read.name == "Read" and read.input == {"file_path": "/work/a.txt"}
    assert isinstance(events[4], ToolResult) and "hi" in events[4].output and events[4].id == read.id
    assert events[5] == Text(text="hi")
    assert events[7] == Done(final_text="hi")


def test_usage_counts_cached_input_as_input_like_codex_does():
    usage = next(e for e in parse(lines("claude_read.jsonl")) if isinstance(e, Usage))

    assert usage.input_tokens == 7 + 10180 + 19357
    assert usage.cached_input_tokens == 19357
    assert usage.output_tokens == 187
    assert usage.cost_usd == 0.0780135


def test_a_failed_run_ends_in_an_error():
    events = parse([{
        "type": "result", "subtype": "error_max_turns", "is_error": True, "usage": {}, "errors": ["hit the turn limit"],
    }])

    assert events[-1] == Done(final_text="", is_error=True, error="hit the turn limit")


def test_subagent_events_stay_out_of_the_stream():
    inner = {"type": "assistant", "parent_tool_use_id": "toolu_1",
             "message": {"content": [{"type": "text", "text": "subagent chatter"}]}}

    assert parse([inner]) == []


def test_read_only_allows_reading_tools_and_crewbots_mcp_only():
    spec = RunSpec(prompt="x", cwd=".", mcp_servers={"crew": McpServer(command="crewbots-mcp")})
    argv = ClaudeCLI().argv(spec)

    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"
    assert argv[argv.index("--allowedTools") + 1] == "Read,Glob,Grep,mcp__crew"
    assert "--strict-mcp-config" in argv
    config = json.loads(argv[argv.index("--mcp-config") + 1])
    assert config == {"mcpServers": {"crew": {"type": "stdio", "command": "crewbots-mcp", "args": [], "env": {}}}}


def test_access_levels_map_to_permission_modes():
    def mode(access):
        argv = ClaudeCLI().argv(RunSpec(prompt="x", cwd=".", access=access))
        return argv[argv.index("--permission-mode") + 1]

    assert mode(Access.WORKSPACE_WRITE) == "acceptEdits"
    assert mode(Access.FULL) == "bypassPermissions"


def test_resume_model_and_instructions_are_passed_and_the_prompt_is_not_an_argument():
    spec = RunSpec(prompt="secret prompt", cwd=".", model="opus", system_prompt="be brief", resume_session_id="s-1")
    argv = ClaudeCLI().argv(spec)

    assert argv[argv.index("--resume") + 1] == "s-1"
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--append-system-prompt") + 1] == "be brief"
    assert "secret prompt" not in argv


async def test_running_sends_the_prompt_on_stdin_and_hides_the_api_key(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")

    events = await collect(ClaudeCLI(command=fake_command("claude_read.jsonl")).run(RunSpec(prompt="hello", cwd=tmp_path)))

    seen = json.loads(record.read_text())
    assert seen["stdin"] == "hello"
    assert "ANTHROPIC_API_KEY" not in seen["env_keys"]
    assert events[-1] == Done(final_text="hi")


async def test_an_explicit_api_key_choice_is_respected(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(record))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-wanted")
    fixture = write_fixture(tmp_path, [{"type": "result", "subtype": "success", "result": "ok", "usage": {}}])

    await collect(ClaudeCLI(command=fake_command(fixture), use_api_key=True).run(RunSpec(prompt="x", cwd=tmp_path)))

    assert "ANTHROPIC_API_KEY" in json.loads(record.read_text())["env_keys"]
