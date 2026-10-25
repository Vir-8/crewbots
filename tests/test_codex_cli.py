import json

import pytest
from helpers import collect, fake_command, kinds, lines

from crewbots.backends import Access, CodexCLI, McpServer, RunSpec
from crewbots.backends.codex_cli import CodexParser
from crewbots.events import Done, Notice, SessionStarted, Text, ToolCall, ToolResult, Usage


def parse(objs: list[dict]):
    parser = CodexParser()
    return [event for obj in objs for event in parser.feed(obj)]


def test_a_real_run_becomes_the_common_events():
    events = parse(lines("codex_read.jsonl"))

    assert kinds(events) == ["session_started", "text", "tool_call", "tool_result", "text", "usage", "done"]
    assert events[0] == SessionStarted(session_id="01a173fb-1c7b-7601-9fb9-c8951b9655fe")
    assert events[2] == ToolCall(id="item_1", name="shell", input={"command": "/bin/zsh -lc 'cat a.txt'"})
    assert events[3] == ToolResult(id="item_1", output="hi\n")
    assert events[5] == Usage(input_tokens=36393, output_tokens=53, cached_input_tokens=31232)
    assert events[6] == Done(final_text="hi")


def test_a_failing_command_is_an_error_result():
    item = {"id": "i", "type": "command_execution", "command": "false", "aggregated_output": "", "exit_code": 1,
            "status": "failed"}

    assert parse([{"type": "item.completed", "item": item}])[-1].is_error


def test_a_file_change_that_only_arrives_completed_still_gets_a_call():
    item = {"id": "f", "type": "file_change", "changes": [{"path": "a.txt", "kind": "update"}], "status": "completed"}

    events = parse([{"type": "item.completed", "item": item}])

    assert events == [
        ToolCall(id="f", name="apply_patch", input={"changes": [{"path": "a.txt", "kind": "update"}]}),
        ToolResult(id="f", output="completed"),
    ]


def test_mcp_calls_are_named_like_claudes():
    item = {"id": "m", "type": "mcp_tool_call", "server": "crew", "tool": "delegate_task", "arguments": {"goal": "x"},
            "status": "completed", "result": {"content": [{"type": "text", "text": "task t-1 queued"}]}}

    events = parse([{"type": "item.started", "item": item}, {"type": "item.completed", "item": item}])

    assert events == [
        ToolCall(id="m", name="mcp__crew__delegate_task", input={"goal": "x"}),
        ToolResult(id="m", output="task t-1 queued"),
    ]


def test_a_failed_turn_ends_in_an_error_and_retries_are_only_notices():
    events = parse([
        {"type": "error", "message": "Reconnecting... 1/5"},
        {"type": "turn.failed", "error": {"message": "usage limit reached"}},
    ])

    assert events == [Notice(message="Reconnecting... 1/5"), Done(final_text="", is_error=True, error="usage limit reached")]


def test_sandbox_goes_through_config_so_resume_works_too():
    argv = CodexCLI().argv(RunSpec(prompt="x", cwd=".", access=Access.WORKSPACE_WRITE, resume_session_id="t-1"))

    assert argv[:4] == ["codex", "exec", "resume", "t-1"]
    assert 'sandbox_mode="workspace-write"' in argv
    assert 'approval_policy="never"' in argv
    assert "-s" not in argv and "-C" not in argv
    assert argv[-1] == "-"


def test_full_access_bypasses_the_sandbox():
    argv = CodexCLI().argv(RunSpec(prompt="x", cwd=".", access=Access.FULL))

    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert not any(a.startswith("sandbox_mode") for a in argv)


def test_mcp_servers_become_config_overrides():
    server = McpServer(command="crewbots-mcp", args=["--bot", "lead"], env={"CREW_TOKEN": "t"})
    argv = CodexCLI().argv(RunSpec(prompt="x", cwd=".", mcp_servers={"crew": server}))

    overrides = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert 'mcp_servers.crew.command="crewbots-mcp"' in overrides
    assert 'mcp_servers.crew.args=["--bot", "lead"]' in overrides
    assert 'mcp_servers.crew.env={"CREW_TOKEN" = "t"}' in overrides


def test_an_mcp_name_that_cannot_be_a_config_key_is_refused():
    with pytest.raises(ValueError):
        CodexCLI().argv(RunSpec(prompt="x", cwd=".", mcp_servers={"my.crew": McpServer(command="x")}))


def test_instructions_go_into_the_first_prompt_only():
    first = CodexCLI().prompt(RunSpec(prompt="do it", cwd=".", system_prompt="you are the reviewer"))
    resumed = CodexCLI().prompt(RunSpec(prompt="again", cwd=".", system_prompt="you are the reviewer",
                                        resume_session_id="t-1"))

    assert first.startswith("<instructions>\nyou are the reviewer\n</instructions>") and first.endswith("do it")
    assert resumed == "again"


async def test_running_sends_the_prompt_on_stdin(tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    monkeypatch.setenv("FAKE_RECORD", str(record))

    events = await collect(CodexCLI(command=fake_command("codex_read.jsonl")).run(RunSpec(prompt="hello", cwd=tmp_path)))

    assert json.loads(record.read_text())["stdin"] == "hello"
    assert events[-1] == Done(final_text="hi")
    assert Text(text="hi") in events
