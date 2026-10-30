"""The Agent SDK backend: its permission policy, its message mapping, its options, and how a session handles
cancels and timeouts (against a fake SDK client, so no test calls a model)."""

import asyncio
import json
from pathlib import Path

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fakes import ScriptedCrew, bot, wait_until

from crewbots import paths
from crewbots.backends import Access, ClaudeAgent, McpServer, RunSpec
from crewbots.backends.claude_sdk import ClaudeAgentSession, ClaudePolicy, SdkMapper, crew_server
from crewbots.backends.policy import READ_ONLY
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


def test_read_only_bots_read_and_look_things_up_in_their_sandbox(worktree):
    policy = ClaudePolicy(Access.READ_ONLY, worktree, {"crewbots"})

    assert policy.check_tool("Read", {"file_path": "/etc/hosts"}) is None
    assert policy.check_tool("ToolSearch", {"query": "select:mcp__blender__get_scene"}) is None  # loads a definition
    assert policy.check_tool("Grep", {"pattern": "x"}) is None
    assert "read-only" in policy.check_tool("Edit", {"file_path": str(worktree / "a.py")})
    assert policy.check_tool("Bash", {"command": "git diff"}) is None  # its sandbox lets it write nothing
    assert "user's to do" in policy.check_tool("Bash", {"command": "git push"})
    assert "no way out" in policy.check_tool("Bash", {"command": "git diff", "dangerouslyDisableSandbox": True})
    assert "nothing wakes you" in policy.check_tool("Bash", {"command": "uv run pytest -q", "run_in_background": True})
    let_write = ClaudePolicy(Access.READ_ONLY, worktree, set(), write_dirs=[worktree / "out"])
    assert "read-only" in let_write.check_tool("Bash", {"command": "ls"})  # no shell then: see claude_cli.shell


def test_writing_bots_write_only_inside_their_task_folder(worktree):
    policy = ClaudePolicy(Access.WORKSPACE_WRITE, worktree, {"crewbots"})

    assert policy.check_tool("Edit", {"file_path": str(worktree / "src" / "a.py")}) is None
    assert "nothing wakes you" in policy.check_tool("Bash", {"command": "uv run pytest -q", "run_in_background": True})
    assert policy.check_tool("Bash", {"command": "uv run pytest -q", "timeout": 600000}) is None
    assert policy.check_tool("Write", {"file_path": "notes.md"}) is None  # relative to the worktree
    assert "own folder" in policy.check_tool("Write", {"file_path": str(worktree.parent / "elsewhere.py")})
    assert "own folder" in policy.check_tool("Edit", {"file_path": str(worktree / ".." / "escape.py")})
    assert "own folder" in policy.check_tool("Edit", {})


def test_no_bot_reads_logins_keys_or_crewbots_own_records(worktree):
    policy = ClaudePolicy(Access.READ_ONLY, worktree, {"crewbots"})
    crew = paths.crew()

    for tool, args in [("Read", {"file_path": str(crew / "daemon.json")}), ("Read", {"file_path": "~/.ssh/id_ed25519"}),
                       ("Glob", {"pattern": "~/.ssh/*"}), ("Glob", {"pattern": f"{crew}/crewbots.db*"}),
                       ("Grep", {"pattern": "key", "path": str(paths.home() / ".aws")})]:
        assert "private" in policy.check_tool(tool, args)
    assert policy.check_tool("Read", {"file_path": str(crew / "team" / "rules.md")}) is None  # the crew's own files
    assert policy.check_tool("Glob", {"pattern": "**/*.py"}) is None
    assert ClaudePolicy(Access.FULL, worktree, set()).check_tool("Read", {"file_path": str(crew / "daemon.json")}) is None


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


def test_the_network_is_off_unless_the_role_card_opens_it(worktree):
    # Claude's sandbox asks for each host a command reaches, as a SandboxNetworkAccess tool call.
    def asks(network, host):
        return ClaudePolicy(Access.WORKSPACE_WRITE, worktree, set(), network=network).check_tool(
            "SandboxNetworkAccess", {"host": host})

    assert "no network access" in asks(False, "pypi.org")
    assert asks(True, "anything.example") is None
    hosts = ("pypi.org", "files.pythonhosted.org", "*.npmjs.org")
    assert asks(hosts, "pypi.org") is None and asks(hosts, "registry.npmjs.org") is None
    assert "isn't one of the hosts" in asks(hosts, "evil.example")
    assert asks(False, "x") is not None and ClaudePolicy(Access.FULL, worktree, set()).check_network("x") is None


def test_web_search_and_pages_follow_the_network_setting(worktree):
    # Claude Code fetches pages itself, outside the shell's sandbox: without this a bot with network off could
    # still reach any site.
    def asks(network, tool, args, access=Access.WORKSPACE_WRITE):
        return ClaudePolicy(access, worktree, set(), network=network).check_tool(tool, args)

    assert "no network access" in asks(False, "WebFetch", {"url": "https://evil.example/x"})
    assert "no network access" in asks(False, "WebSearch", {"query": "x"})
    assert asks(True, "WebSearch", {"query": "x"}) is None
    assert asks(True, "WebFetch", {"url": "https://docs.python.org"}, access=Access.READ_ONLY) is None
    assert asks(("docs.python.org",), "WebFetch", {"url": "https://docs.python.org/3/"}) is None
    assert "isn't one of the hosts" in asks(("docs.python.org",), "WebFetch", {"url": "https://evil.example"})
    assert "limited to some hosts" in asks(("docs.python.org",), "WebSearch", {"query": "x"})


def test_a_read_only_bot_gets_the_web_tools_only_with_network(worktree):
    def tools(network):
        spec = RunSpec(prompt="x", cwd=worktree, access=Access.READ_ONLY, network=network)
        return ClaudeAgent(cli_path="claude").options(spec, ClaudeAgentSession(spec)).tools

    assert tools(False) == ["Read", "Glob", "Grep", "ToolSearch", "Bash"]
    assert tools(True) == ["Read", "Glob", "Grep", "ToolSearch", "Bash", "WebFetch", "WebSearch"]


async def test_a_refusal_is_reported_and_tells_claude_why(worktree):
    policy = ClaudePolicy(Access.READ_ONLY, worktree, set())

    decision = await policy("Write", {"file_path": str(worktree / "build.py")}, None)

    assert decision.behavior == "deny" and "read-only" in decision.message
    assert policy.take_refusals() == [f"Refused Write: {READ_ONLY}"]
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


def notified():
    return TaskNotificationMessage(subtype="task_notification", data={}, task_id="b1", status="completed",
                                   output_file="", summary="", uuid="u", session_id="s-1")


def test_a_turn_claude_code_started_itself_is_not_the_bots_reply():
    # A background command finished between turns: Claude Code tells the model in a turn of its own first.
    injected = {"kind": "task-notification", "producer": "session-task"}
    mapper = SdkMapper()
    events = [e for m in [notified(), AssistantMessage(content=[TextBlock(text="The command finished.")], model="m"),
                          result("The command finished.", origin=injected),
                          AssistantMessage(content=[TextBlock(text="second")], model="m"), result("second")]
              for e in mapper.feed(m)]

    assert [e for e in events if isinstance(e, Text)] == [Text(text="second")]
    assert sum(isinstance(e, Usage) for e in events) == 2  # its tokens still count
    assert [e for e in events if isinstance(e, Done)] == [Done(final_text="second")]


def test_a_notification_folded_into_the_turn_still_shows_the_reply():
    mapper = SdkMapper()
    events = [e for m in [notified(), AssistantMessage(content=[TextBlock(text="all done")], model="m"),
                          result("all done")] for e in mapper.feed(m)]

    assert Text(text="all done") in events and events[-1] == Done(final_text="all done")


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
    assert options.tools == ["Read", "Glob", "Grep", "ToolSearch", "Bash"] and options.permission_mode == "default"
    assert options.sandbox["enabled"] and "network" not in options.sandbox  # a shell to look things up, no servers
    deny = json.loads(options.settings)["permissions"]["deny"]
    folders = (tmp_path.resolve(), (tmp_path / "skills").resolve())
    assert {f"Edit(/{folder}/**)" for folder in folders} <= set(deny)  # its commands write in neither
    assert options.can_use_tool is session.policy
    assert set(options.mcp_servers) == {"resolve"}  # no crew tools without a CrewTools
    assert options.env["ANTHROPIC_API_KEY"] == "" and options.env["GH_TOKEN"] == ""  # no logins in its shell
    assert options.add_dirs == [str(tmp_path / "skills")]


def test_an_explicit_context_window_becomes_autocompact(tmp_path):
    spec = RunSpec(prompt="", cwd=tmp_path, context_window=200_000)

    assert ClaudeAgent(cli_path="/bin/claude").options(spec, ClaudeAgentSession(spec)).extra_args == {"autocompact": "200000"}


def test_full_access_skips_the_policy(tmp_path):
    spec = RunSpec(prompt="", cwd=tmp_path, access=Access.FULL)

    options = ClaudeAgent(cli_path="/bin/claude").options(spec, ClaudeAgentSession(spec))

    assert options.permission_mode == "bypassPermissions" and options.can_use_tool is None
    assert json.loads(options.settings) == {"includeCoAuthoredBy": False, "attribution": {"commit": "", "pr": ""}}
    assert options.sandbox is None


def test_claude_keeps_its_file_tools_and_its_shell_out_of_the_private_paths(tmp_path):
    spec = RunSpec(prompt="", cwd=tmp_path, access=Access.WORKSPACE_WRITE)

    options = ClaudeAgent(cli_path="/bin/claude").options(spec, ClaudeAgentSession(spec))

    deny = json.loads(options.settings)["permissions"]["deny"]
    assert f"Read(/{paths.crew() / 'daemon.json'})" in deny and f"Read(/{paths.home() / '.ssh'}/**)" in deny
    assert options.sandbox["enabled"] and not options.sandbox["allowUnsandboxedCommands"]
    assert options.sandbox["network"] == {"allowLocalBinding": True}  # to run what it checks: a dev server, a daemon


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

    async def receive_messages(self):
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


async def test_prompt_dispatch_follows_sdk_query():
    client = FakeClient([result("hi")])
    sent = []
    session = ClaudeAgentSession(RunSpec(prompt="", cwd=Path("."),
                                         on_prompt_sent=lambda: sent.append(list(client.queries))))
    session.client = client
    await collect(session.turn("hello"))
    assert sent == [["hello"]]


async def test_a_turn_waits_for_its_own_result_past_one_claude_code_started():
    client = FakeClient([result("background done", origin={"kind": "task-notification"}), result("hi")])
    session = session_with(client)

    events = await collect(session.turn("hello"))

    assert events[-1] == Done(final_text="hi")


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
        async def receive_messages(self):
            await session.policy("Write", {"file_path": str(worktree / "a.py")}, None)
            yield result("couldn't")

    session.client = Refusing([])
    events = await collect(session.turn("clean up"))

    assert Notice(message=f"Refused Write: {READ_ONLY}") in events


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

    assert {d.name for d in tools.definitions()} == {  # a thread's: no verdicts, names or routines
        "handoff", "escalate", "remember", "forget", "recall", "start_thread", "post", "delegate", "tasks",
        "message", "stop_task", "ask", "open_group", "add_to_group", "working_state", "update_state", "share",
        "write_page", "edit_page", "ask_permission", "browser_open", "browser_screenshot",
        "browser_click", "browser_type", "browser_evaluate", "browser_console", "open_unity_editor"}
    with pytest.raises(ToolRefused, match="not one of this bot's crew tools"):
        await tools.call("propose_merge", {"task_id": "t_x", "summary": "x"})
    reply = await tools.call("handoff", {"done": "built", "output": ["a.py"]})
    assert reply == "Handoff recorded. End your reply with a short summary."
    assert store.latest_handoff(task.id).output == ["a.py"]
    await tools.call("handoff", {"done": "built", "output": "a.py", "evidence": "- pytest: 3 passed\n- opened the page"})
    handed = store.latest_handoff(task.id)  # one item sent bare, or a line each, is still a list
    assert handed.output == ["a.py"] and handed.evidence == ["pytest: 3 passed", "opened the page"]
    assert (await tools.call("escalate", {"question": "Which?"})).endswith("the answer arrives as your next message.")


def test_the_crew_server_holds_exactly_the_bots_tools(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    server = crew_server(CrewTools(crew.runner, store.create_task("engineer", "x").id, can_delegate=False))

    assert server["type"] == "sdk" and server["name"] == "crewbots"


def test_reasoning_effort_goes_to_claude_when_it_takes_it(worktree):
    def effort(value):
        spec = RunSpec(prompt="", cwd=worktree, effort=value)
        return ClaudeAgent().options(spec, ClaudeAgentSession(spec)).effort

    assert effort("high") == "high" and effort(None) is None
    assert effort("ultra") is None  # a Codex-only level: Claude's own default instead


def test_policy_keeps_other_bots_working_state_private(tmp_path):
    from crewbots.backends.policy import Policy

    root = tmp_path / "memory"
    own = root / "worker" / "working-state" / "state.md"
    other = root / "chief" / "working-state" / "state.md"
    policy = Policy(Access.WORKSPACE_WRITE, tmp_path / "work", set(), deny_read=[other.parent])
    assert policy.check_tool("Read", {"file_path": str(own)}) is None
    assert policy.check_tool("Read", {"file_path": str(other)}) is not None
    assert policy.check_tool("Grep", {"path": str(root), "pattern": "anything"}) is not None
    assert policy.check_tool("Glob", {"pattern": str(root / "*" / "state.md")}) is not None
    full_policy = Policy(Access.FULL, tmp_path / "work", set(), deny_read=[other.parent])
    assert full_policy.check_tool("Read", {"file_path": str(other)}) is not None


def test_private_state_paths_are_carried_to_backends(store, tmp_path):
    from crewbots.backends.claude_cli import settings
    from crewbots.backends.codex_cli import sandbox_overrides
    from crewbots.backends.policy import Policy

    crew = ScriptedCrew(store, tmp_path, bot("worker"), bot("chief"))
    task = crew.runner.chat("worker")
    spec = crew.runner._spec(task, crew.runner.crew.bots["worker"], tmp_path, "token", "", crew.backend)
    chief_state = tmp_path / "memory" / "chief" / "working-state" / "state.md"
    own_state = tmp_path / "memory" / "worker" / "working-state" / "state.md"
    assert chief_state.parent in spec.deny_read and own_state.parent not in spec.deny_read
    assert str(chief_state.parent) in str(settings(spec.access, spec.write_dirs, spec.deny_read))
    assert str(chief_state.parent) in str(sandbox_overrides(spec))
    policy = Policy(spec.access, spec.cwd, set(), deny_read=spec.deny_read)
    assert policy.check_tool("Read", {"file_path": str(chief_state)}) is not None
    assert policy.check_tool("Read", {"file_path": str(own_state)}) is None


def test_other_bot_temporary_state_files_are_denied(tmp_path):
    from crewbots.backends.policy import Policy

    other = tmp_path / "memory" / "chief" / "working-state"
    other.mkdir(parents=True)
    temporary = other / ".state-abandoned"
    temporary.write_text("secret")
    own = tmp_path / "memory" / "worker" / "working-state" / ".state-own"
    policy = Policy(Access.WORKSPACE_WRITE, tmp_path, set(), deny_read=[other])
    assert policy.check_tool("Read", {"file_path": str(temporary)}) is not None
    assert policy.check_tool("Glob", {"path": str(other), "pattern": ".state-*"}) is not None
    assert policy.check_tool("Read", {"file_path": str(own)}) is None


# Steering: a message sent into a turn under way


class LiveClient:
    """A fake SDK client whose messages arrive when the test sends them."""

    def __init__(self):
        self.queries: list[str] = []
        self.messages: asyncio.Queue = asyncio.Queue()

    async def query(self, prompt):
        self.queries.append(prompt)

    async def receive_messages(self):
        while True:
            yield await self.messages.get()

    async def interrupt(self):
        pass

    async def disconnect(self):
        pass


async def steered_turn(monkeypatch, feed):
    """Runs a turn, steering "and use CSV" into it after its first step, then `feed`s the client the rest."""
    import crewbots.backends.claude_sdk as sdk

    monkeypatch.setattr(sdk, "STEER_GRACE_S", 0.05)
    client = LiveClient()
    session = session_with(client)
    assert not await session.steer("too early")  # no turn under way
    stream = session.turn("export the report")
    client.messages.put_nowait(AssistantMessage(content=[ToolUseBlock(id="t1", name="Bash", input={})], model="m"))
    assert await anext(stream) == ToolCall(id="t1", name="Bash", input={})
    assert session.steerable and await session.steer("and use CSV")
    reading = asyncio.create_task(collect(stream))  # the turn reads on while the messages come
    await feed(client, session)
    return client, session, await asyncio.wait_for(reading, 2)


async def test_a_message_taken_in_at_a_step_ends_the_turn_with_one_result(monkeypatch):
    async def feed(client, session):
        client.messages.put_nowait(result("Exported it as CSV"))  # Claude Code folded it in: one result, then nothing

    client, session, events = await steered_turn(monkeypatch, feed)
    assert client.queries == ["export the report", "and use CSV"]
    assert [e for e in events if isinstance(e, Done)] == [Done(final_text="Exported it as CSV")]
    assert not session.steerable and not await session.steer("after")


async def test_a_message_too_late_for_a_step_is_answered_in_the_same_turn(monkeypatch):
    async def feed(client, session):
        for message in (result("Exported it as JSON"), SystemMessage(subtype="init", data={"session_id": "s-1"}),
                        AssistantMessage(content=[TextBlock(text="Now as CSV too")], model="m"), result("Now as CSV too")):
            client.messages.put_nowait(message)

    _, _, events = await steered_turn(monkeypatch, feed)
    assert [e for e in events if isinstance(e, Done)] == [Done(final_text="Exported it as JSON\n\nNow as CSV too")]
    assert len([e for e in events if isinstance(e, Usage)]) == 2  # both answers cost tokens


async def test_a_message_steered_while_the_turn_waits_is_answered_too(monkeypatch):
    async def feed(client, session):
        client.messages.put_nowait(result("Exported it"))  # "and use CSV" was taken in: nothing more for it
        await asyncio.sleep(0.03)
        assert await session.steer("and email it")  # in the wait after it: Claude Code answers in a turn of its own
        client.messages.put_nowait(SystemMessage(subtype="init", data={"session_id": "s-1"}))
        await asyncio.sleep(0.15)  # longer than the wait: once its turn has begun, the turn waits for its answer
        client.messages.put_nowait(result("Emailed it"))

    client, _, events = await steered_turn(monkeypatch, feed)
    assert client.queries[-1] == "and email it"
    assert [e for e in events if isinstance(e, Done)] == [Done(final_text="Exported it\n\nEmailed it")]


def test_a_turns_answers_leave_out_the_silent_ones():
    from crewbots.backends.claude_sdk import _answers

    assert _answers(["NO_REPLY", "On it"]) == "On it"
    assert _answers(["Here it is", ""]) == "Here it is"
    assert _answers(["NO_REPLY", "NO_REPLY"]) == "NO_REPLY"  # still silent
