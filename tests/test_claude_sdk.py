"""The Agent SDK backend: its permission policy, its message mapping, its options, and how a session handles
cancels and timeouts (against a fake SDK client, so no test calls a model)."""

import asyncio
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fakes import ScriptedCrew, bot, wait_until

from crewbots.backends import Access, ClaudeAgent, McpServer, RunSpec
from crewbots.backends.claude_sdk import ClaudeAgentSession, ClaudePolicy, SdkMapper, crew_server
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done, Notice, SessionStarted, Text, ToolCall, ToolResult, Usage
from crewbots.store import Store


def result(text="ok", **kw):
    fields = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s-1",
                  total_cost_usd=0.01, usage={"input_tokens": 5, "cache_read_input_tokens": 10, "output_tokens": 2},
                  result=text)
    fields.update(kw)
    return ResultMessage(**fields)


# The policy


@pytest.fixture
def worktree(tmp_path):
    path = tmp_path / "wt"
    path.mkdir()
    return path


def test_read_only_bots_read_and_nothing_else(worktree):
    policy = ClaudePolicy(Access.READ_ONLY, worktree, {"crewbots"})

    assert policy.check_tool("Read", {"file_path": "/etc/hosts"}) is None
    assert policy.check_tool("Grep", {"pattern": "x"}) is None
    assert "read-only" in policy.check_tool("Edit", {"file_path": str(worktree / "a.py")})
    assert "read-only" in policy.check_tool("Bash", {"command": "ls"})


def test_writing_bots_write_only_inside_their_task_folder(worktree):
    policy = ClaudePolicy(Access.WORKSPACE_WRITE, worktree, {"crewbots"})

    assert policy.check_tool("Edit", {"file_path": str(worktree / "src" / "a.py")}) is None
    assert policy.check_tool("Write", {"file_path": "notes.md"}) is None  # relative to the worktree
    assert "own folder" in policy.check_tool("Write", {"file_path": str(worktree.parent / "elsewhere.py")})
    assert "own folder" in policy.check_tool("Edit", {"file_path": str(worktree / ".." / "escape.py")})
    assert "own folder" in policy.check_tool("Edit", {})


@pytest.mark.parametrize("command", [
    "git push origin main", "git -C repo merge feature && echo ok", "git --no-pager push", "gh pr create --fill",
    "npm publish", "git reset --hard HEAD~3", "cd x; git rebase main",
])
def test_nobody_pushes_merges_or_publishes(worktree, command):
    assert "user's to do" in ClaudePolicy(Access.WORKSPACE_WRITE, worktree, set()).check_tool("Bash", {"command": command})


def test_ordinary_commands_and_git_reads_are_fine(worktree):
    policy = ClaudePolicy(Access.WORKSPACE_WRITE, worktree, set())

    for command in ("pytest -q", "git status", "git diff --stat", "git log -3", "npm test"):
        assert policy.check_tool("Bash", {"command": command}) is None


def test_only_granted_mcp_servers(worktree):
    policy = ClaudePolicy(Access.READ_ONLY, worktree, {"crewbots", "resolve"})

    assert policy.check_tool("mcp__crewbots__handoff", {}) is None
    assert policy.check_tool("mcp__resolve__cut", {}) is None
    assert "isn't granted" in policy.check_tool("mcp__kite__place_order", {})


async def test_a_refusal_is_reported_and_tells_claude_why(worktree):
    policy = ClaudePolicy(Access.READ_ONLY, worktree, set())

    decision = await policy("Bash", {"command": "rm -rf build"}, None)

    assert decision.behavior == "deny" and "read-only" in decision.message
    assert policy.take_refusals() == ["Refused Bash: this bot is read-only (its role card says access: read_only)"]
    assert policy.take_refusals() == []


# Mapping SDK messages to events


def test_sdk_messages_become_the_common_events():
    mapper = SdkMapper()
    events = [e for m in [
        SystemMessage(subtype="init", data={"session_id": "s-9"}),
        AssistantMessage(
            content=[TextBlock(text="Looking."), ToolUseBlock(id="t1", name="Read", input={"file_path": "a"})], model="m",
        ),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="contents", is_error=False)]),
        AssistantMessage(content=[TextBlock(text="hi")], model="m", parent_tool_use_id="sub"),  # a subagent: skipped
        result("all done"),
    ] for e in mapper.feed(m)]

    assert events == [
        SessionStarted(session_id="s-9"), Text(text="Looking."), ToolCall(id="t1", name="Read", input={"file_path": "a"}),
        ToolResult(id="t1", output="contents"), Usage(input_tokens=15, output_tokens=2, cached_input_tokens=10, cost_usd=0.01),
        Done(final_text="all done"),
    ]


def test_a_failed_result_is_an_error():
    events = SdkMapper().feed(result("", subtype="error_max_turns", is_error=True, errors=["hit the turn limit"]))

    assert events[-1] == Done(final_text="", is_error=True, error="hit the turn limit")


# Options


def test_the_cli_gets_only_crewbots_tools_and_the_repos_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-for-bots")
    spec = RunSpec(prompt="", cwd=tmp_path, access=Access.READ_ONLY, model="sonnet", system_prompt="You review.",
                   mcp_servers={"resolve": McpServer(command="resolve-mcp")}, read_dirs=[tmp_path / "skills"])
    session = ClaudeAgentSession(spec)

    options = ClaudeAgent(cli_path="/bin/claude").options(spec, session)

    assert options.strict_mcp_config and options.setting_sources == ["project", "local"]
    assert options.system_prompt == {"type": "preset", "preset": "claude_code", "append": "You review."}
    assert options.tools == ["Read", "Glob", "Grep"] and options.permission_mode == "default"
    assert options.can_use_tool is session.policy
    assert set(options.mcp_servers) == {"resolve"}  # no crew tools without a CrewTools
    assert options.env == {"ANTHROPIC_API_KEY": ""}
    assert options.add_dirs == [str(tmp_path / "skills")]


def test_full_access_skips_the_policy(tmp_path):
    spec = RunSpec(prompt="", cwd=tmp_path, access=Access.FULL)

    options = ClaudeAgent(cli_path="/bin/claude").options(spec, ClaudeAgentSession(spec))

    assert options.permission_mode == "bypassPermissions" and options.can_use_tool is None


# A session, with a fake SDK client


class FakeClient:
    def __init__(self, script, slow=False):
        self.script = script  # messages for each turn
        self.queries: list[str] = []
        self.interrupted = 0
        self.disconnected = False
        self.slow = slow
        self._release = asyncio.Event()

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_response(self):
        for message in self.script:
            if self.slow and isinstance(message, ResultMessage):
                await self._release.wait()  # held until interrupted
            yield message

    async def interrupt(self):
        self.interrupted += 1
        self._release.set()

    async def disconnect(self):
        self.disconnected = True


def session_with(client, timeout_s=None, tmp=Path(".")):
    session = ClaudeAgentSession(RunSpec(prompt="", cwd=tmp, timeout_s=timeout_s))
    session.client = client
    return session


async def collect(stream):
    return [e async for e in stream]


async def test_a_turn_ends_with_done_and_the_session_stays_open():
    client = FakeClient([SystemMessage(subtype="init", data={"session_id": "s-1"}), result("hi")])
    session = session_with(client)

    events = await collect(session.turn("hello"))

    assert client.queries == ["hello"] and events[-1] == Done(final_text="hi")
    assert session.alive and session.session_id == "s-1"


async def test_a_timeout_interrupts_the_turn_instead_of_killing_the_session():
    client = FakeClient([AssistantMessage(content=[TextBlock(text="working")], model="m"), result("partial")], slow=True)
    session = session_with(client, timeout_s=0.2)

    events = await collect(session.turn("long job"))

    assert client.interrupted == 1
    assert events[-1].is_error and "timed out" in events[-1].error
    assert session.alive and not client.disconnected


async def test_abandoning_a_turn_interrupts_it_and_keeps_the_session():
    client = FakeClient([AssistantMessage(content=[TextBlock(text="step 1")], model="m"), result("stopped")], slow=True)
    session = session_with(client)
    stream = session.turn("long job")

    assert await anext(stream) == Text(text="step 1")
    await stream.aclose()  # what cancelling the task does

    assert client.interrupted == 1 and session.alive and not client.disconnected


async def test_a_cli_that_dies_mid_turn_ends_the_session():
    client = FakeClient([AssistantMessage(content=[TextBlock(text="half")], model="m")])  # no result: it died
    session = session_with(client)

    events = await collect(session.turn("x"))

    assert events[-1].is_error and not session.alive and client.disconnected


async def test_refusals_during_a_turn_become_notices(worktree):
    session = ClaudeAgentSession(RunSpec(prompt="", cwd=worktree, access=Access.READ_ONLY))

    class Refusing(FakeClient):
        async def receive_response(self):
            await session.policy("Bash", {"command": "rm -rf ."}, None)
            yield result("couldn't")

    session.client = Refusing([])
    events = await collect(session.turn("clean up"))

    assert Notice(message="Refused Bash: this bot is read-only (its role card says access: read_only)") in events


# The in-process crew tools


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


async def test_crew_tools_act_as_their_task_without_a_server_or_token(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    chief = store.create_task("chief", "lead")
    tools = CrewTools(crew.runner, chief.id, can_delegate=True)

    reply = await tools.call("delegate", {"bot": "engineer", "goal": "build it"})

    assert reply.startswith("Delegated t_") and store.children(chief.id)[0].bot == "engineer"
    with pytest.raises(ToolRefused, match="no bot named 'ghost'"):
        await tools.call("delegate", {"bot": "ghost", "goal": "x"})
    with pytest.raises(ToolRefused, match="needs goal"):
        await tools.call("delegate", {"bot": "engineer"})
    await wait_until(lambda: not store.children(chief.id)[0].active)


async def test_workers_cant_reach_coordinator_tools(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    task = store.create_task("engineer", "x")
    tools = CrewTools(crew.runner, task.id, can_delegate=False)

    assert {d.name for d in tools.definitions()} == {"handoff", "escalate", "review"}
    with pytest.raises(ToolRefused, match="not one of this bot's crew tools"):
        await tools.call("delegate", {"bot": "engineer", "goal": "x"})
    reply = await tools.call("handoff", {"done": "built", "output": ["a.py"]})
    assert reply == "Handoff recorded. End your reply with a short summary."
    assert store.latest_handoff(task.id).output == ["a.py"]


def test_the_crew_server_holds_exactly_the_bots_tools(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    server = crew_server(CrewTools(crew.runner, "t_x", can_delegate=False))

    assert server["type"] == "sdk" and server["name"] == "crewbots"
