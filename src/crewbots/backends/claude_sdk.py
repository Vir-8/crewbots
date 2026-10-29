"""Claude through the Claude Agent SDK, the official way to drive Claude Code from a program.

The SDK still runs the `claude` binary (the user's own, signed in, so a Pro/Max plan works), but over a control
protocol a plain `claude -p` doesn't have, which gives crewbots three things:

- **Interrupt.** A turn can be stopped (cancel, timeout) without killing the process, so the session stays warm.
- **In-process crew tools.** crewbots' tools run in the daemon as an SDK MCP server: no subprocess, no token.
- **A say on every tool call.** `can_use_tool` applies crewbots' policy (`backends/policy.py`), the same rules Codex
  bots get through their approval requests.
"""

import asyncio
import contextlib
import json
import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from crewbots.backends.base import CLAUDE_BOT_ENV, Access, RunSpec, claude_retry, package_caches
from crewbots.backends.claude_cli import (
    EDIT_TOOLS,
    READ_ONLY_TOOLS,
    _images,
    _stringify,
    compacted,
    rate_limit,
    settings,
    shell,
)
from crewbots.backends.credentials import without_credentials
from crewbots.backends.policy import WEB_TOOLS, Policy
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done, Event, Notice, SessionStarted, Text, ToolCall, ToolResult, Usage

CREW_SERVER = "crewbots"
INTERRUPT_GRACE_S = 20.0  # after an interrupt, how long the turn may take to wind down before the session is dropped
# After a result, while a message steered into the turn has none of its own yet: a message that came too late for the
# turn's last step gets a turn of its own, which Claude Code starts at once (its init comes with the result); one
# taken in at a step gets nothing more.
STEER_GRACE_S = 3.0
_END = object()


class ClaudeAgent:
    name = "claude"
    in_process_tools = True  # the runner hands it the crew tools as Python, not as an MCP server

    def __init__(self, cli_path: str | None = None, use_api_key: bool = False):
        """`use_api_key=False` keeps ANTHROPIC_API_KEY from the CLI, so a key left in the environment never silently
        moves a run from the subscription to API billing."""
        self.cli_path = cli_path or shutil.which("claude")
        self.use_api_key = use_api_key

    async def open(self, spec: RunSpec) -> "ClaudeAgentSession":
        session = ClaudeAgentSession(spec)
        client = ClaudeSDKClient(self.options(spec, session))
        await client.connect()
        session.client = client
        return session

    async def run(self, spec: RunSpec) -> AsyncIterator[Event]:
        """One turn on its own: a session opened for it and closed after."""
        session = await self.open(spec)
        try:
            async for event in session.turn(spec.prompt):
                yield event
        finally:
            await session.close()

    def options(self, spec: RunSpec, session: "ClaudeAgentSession") -> ClaudeAgentOptions:
        servers: dict[str, Any] = {name: s.config() for name, s in spec.mcp_servers.items() if name != CREW_SERVER}
        if spec.crew is not None:  # the crew's tools, or a reviewer's
            servers[CREW_SERVER] = crew_server(spec.crew)
        full = spec.access is Access.FULL
        env = dict(CLAUDE_BOT_ENV)
        if not self.use_api_key and "ANTHROPIC_API_KEY" in os.environ:
            env["ANTHROPIC_API_KEY"] = ""
        # Claude Code's OS sandbox for shell commands: writes confined to the working folder (a read-only bot's to its
        # temp folder alone), and no opting out. Every command still goes through can_use_tool (the policy), since
        # auto-allowing is off. The sandbox points TMPDIR at /tmp/claude, which it doesn't create; without it, tools
        # needing a temp folder fail.
        if sandbox := shell(spec.access, spec.write_dirs):
            Path("/tmp/claude").mkdir(exist_ok=True)
            env |= without_credentials() | (package_caches() if spec.access is Access.WORKSPACE_WRITE else {})
        return ClaudeAgentOptions(
            cli_path=self.cli_path,
            cwd=str(spec.cwd),
            model=spec.model,
            system_prompt={"type": "preset", "preset": "claude_code", "append": spec.system_prompt}
            if spec.system_prompt else None,
            resume=spec.resume_session_id,
            # Only crewbots' servers and the repo's settings: never the user's own servers, hooks or plugins.
            setting_sources=["project", "local"],
            strict_mcp_config=True,
            mcp_servers=servers,
            add_dirs=[str(d) for d in [*spec.read_dirs, *spec.write_dirs]],
            # A read-only bot gets the web tools too when it has network (the policy checks each call's host), and
            # the file tools when the user let it write in a folder (the policy keeps them to it).
            tools=[*READ_ONLY_TOOLS.split(","), *(["Bash"] if sandbox else []),
                   *(EDIT_TOOLS.split(",") if spec.write_dirs else []), *(sorted(WEB_TOOLS) if spec.network else [])]
            if spec.access is Access.READ_ONLY else None,
            permission_mode="bypassPermissions" if full else "default",
            can_use_tool=None if full else session.policy,
            settings=json.dumps(extra) if (extra := settings(
                spec.access, spec.write_dirs, spec.deny_read,
                [Path(folder).resolve() for folder in (spec.cwd, *spec.read_dirs)])) else None,
            env=env,
            sandbox=sandbox,
            effort=spec.effort if spec.effort in CLAUDE_EFFORTS else None,
            # The window it compacts within, and /context's percentage is of this rather than the model's whole window.
            extra_args={"autocompact": str(spec.context_window)} if spec.context_window else {},
            stderr=session.stderr_lines.append,
        )


CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ClaudeAgentSession:
    interruptible = True

    def __init__(self, spec: RunSpec):
        self.client: ClaudeSDKClient | None = None
        self.on_prompt_sent = spec.on_prompt_sent
        self.session_id = spec.resume_session_id
        self.timeout_s = spec.timeout_s
        self.policy = ClaudePolicy(spec.access, Path(spec.cwd), set(spec.mcp_servers) | {CREW_SERVER},
                                   network=spec.network, write_dirs=spec.write_dirs, deny_read=spec.deny_read)
        self.stderr_lines: list[str] = []
        self.temp: Path | None = None
        self._broken = False
        # A turn's prompts: its own and any steered into it, and how many have their result. Claude Code takes a steered
        # message at the turn's next step, or answers it straight after, and the turn reads on until each has its result.
        self._asked = self._answered = 0
        self.steerable = False  # a turn is under way that a message can be steered into

    @property
    def alive(self) -> bool:
        return self.client is not None and not self._broken

    async def steer(self, text: str) -> bool:
        """Adds a message to the turn under way; False if no turn is open to take it."""
        if not self.steerable or not self.client:
            return False
        self._asked += 1  # before anything else runs: the turn now waits for this one's result too
        try:
            await self.client.query(text)
        except Exception:
            self._broken = True
            return False
        return True

    async def turn(self, prompt: str) -> AsyncIterator[Event]:
        assert self.client
        mapper = SdkMapper()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        settled = False

        async def pump() -> None:
            try:
                # Until the result of this turn's prompts, past any turn Claude Code started itself in between.
                async for message in self.client.receive_messages():
                    await queue.put(message)
                    if isinstance(message, ResultMessage) and ours(message):
                        self._answered += 1
                        if self._answered >= self._asked:
                            self.steerable = False
                            break
            except Exception as e:  # the CLI died, or the protocol broke
                await queue.put(e)
            finally:
                await queue.put(_END)

        self._asked, self._answered, self.steerable = 1, 0, True
        try:
            await self.client.query(prompt)
            if self.on_prompt_sent:
                self.on_prompt_sent()
        except Exception as e:
            self._broken = True
            self.steerable = False
            yield Done(final_text="", is_error=True, error=f"claude: {e}", retry="transient")
            return
        reader = asyncio.create_task(pump())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_s if self.timeout_s else None
        timed_out = False
        answered = 0
        held: Done | None = None  # the latest result's, while a steered message may yet get one of its own
        asked = 0  # how many prompts the turn had when it was held
        said: list[str] = []  # earlier answers in the turn, when a steered message got an answer of its own
        try:
            while True:
                for notice in self.policy.take_refusals():
                    yield Notice(message=notice)
                remaining = None if deadline is None else deadline - loop.time()
                try:
                    wait = STEER_GRACE_S if held else None if remaining is None else max(0.0, remaining)
                    message = await asyncio.wait_for(queue.get(), wait)
                except TimeoutError:
                    if held and self._asked > asked:  # another came in meanwhile: wait for its answer too
                        asked = self._asked
                        continue
                    if held:  # the steered messages were taken in at a step of this turn: nothing more is coming
                        settled = True
                        yield held
                        return
                    timed_out = True
                    deadline = None
                    await self._interrupt()
                    continue
                if message is _END:
                    settled = True
                    for notice in self.policy.take_refusals():
                        yield Notice(message=notice)
                    if not mapper.done:
                        self._broken = True
                        detail = " ".join(self.stderr_lines[-5:]) or "the CLI stopped"
                        yield Done(final_text=mapper.final_text, is_error=True, error=f"claude: {detail}",
                                   retry="transient")
                    return
                if isinstance(message, Exception):
                    self._broken = True
                    yield Done(final_text=mapper.final_text, is_error=True, error=f"claude: {message}",
                               retry="transient")
                    settled = True
                    return
                if held:  # more is coming: a steered message's own turn, whose answer goes with this one
                    said, held = [*said, held.final_text], None
                answered = answered + 1 if isinstance(message, ResultMessage) and ours(message) else answered
                for event in mapper.feed(message, timed_out=timed_out, timeout_s=self.timeout_s):
                    if isinstance(event, SessionStarted):
                        self.session_id = event.session_id
                    if isinstance(event, Done) and said:
                        event = replace(event, final_text=_answers([*said, event.final_text]))
                    if isinstance(event, Done) and answered < self._asked:
                        held, asked = event, self._asked  # a steered message may still have its answer to come
                        continue
                    yield event
        finally:
            self.steerable = False
            if not settled:
                # Abandoned mid-turn (cancelled): stop the turn but keep the session, if Claude winds down in time.
                await self._interrupt()
                try:
                    await asyncio.wait_for(reader, INTERRUPT_GRACE_S)
                except (TimeoutError, asyncio.CancelledError):
                    self._broken = True
            if not reader.done():
                reader.cancel()
            if self._broken:
                await self.close()

    def compact(self, instructions: str) -> AsyncIterator[Event]:
        """Compacts the conversation in place, the way /compact does, with `instructions` for what to keep."""
        return self.turn(f"/compact {instructions}")

    async def context_percent(self) -> float | None:
        """How full the conversation's context window is, as Claude Code's /context counts it."""
        if not self.client:
            return None
        try:
            usage = await self.client.get_context_usage()
            return float(usage.get("percentage"))
        except Exception:
            return None

    async def _interrupt(self) -> None:
        if self.client:
            with contextlib.suppress(Exception):
                await self.client.interrupt()

    async def close(self) -> None:
        self._broken = True
        client, self.client = self.client, None
        if client:
            with contextlib.suppress(Exception):
                await client.disconnect()
        if self.temp:
            shutil.rmtree(self.temp, ignore_errors=True)


class ClaudePolicy(Policy):
    """The shared policy, asked the way Claude asks: one tool call at a time, through `can_use_tool`."""

    async def __call__(self, name: str, args: dict[str, Any], context: ToolPermissionContext):
        reason = self.check_tool(name, args)
        if reason is None:
            return PermissionResultAllow()
        return PermissionResultDeny(message=self.refused(name, reason))


def _answers(texts: list[str]) -> str:
    """A turn's answers, when a message steered into it was answered on its own: each that says something, in order."""
    from crewbots.crew import is_no_reply  # crew loads the backends

    return "\n\n".join(t for t in texts if t.strip() and not is_no_reply(t)) or texts[-1]


def ours(result: ResultMessage) -> bool:
    """Whether a result is of a prompt crewbots sent, rather than of a turn Claude Code started itself: when a
    background command finishes between turns, Claude Code tells the model in a turn of its own, whose result would
    otherwise end crewbots' next turn early, with its text."""
    return (result.origin or {}).get("kind", "human") == "human"


class SdkMapper:
    """Agent SDK messages to crewbots events, the same ones every backend gives."""

    def __init__(self) -> None:
        self.final_text = ""
        self.done = False
        self.api_error: str | None = None  # the kind of the last API error Claude Code gave up on, if any
        self.started = False  # this turn's prompt has had a reply
        self.injected = False  # in a turn Claude Code started before this one's: its words aren't the bot's reply

    def feed(self, message: Any, timed_out: bool = False, timeout_s: float | None = None) -> list[Event]:
        if getattr(message, "parent_tool_use_id", None):
            return []  # inside a subagent; its call and result already appear at the top level
        match message:
            case TaskNotificationMessage() if not self.started:
                self.injected = True  # a background command finished; Claude Code answers that first
                return []
            case ResultMessage() if not ours(message):
                # Whatever came before it was that turn's, since this turn's prompt waits behind it.
                self.injected = self.started = False
                self.final_text = ""
                return [_usage(message)]  # it spent tokens all the same
            case SystemMessage(subtype="init", data=data):
                return [SessionStarted(session_id=data.get("session_id", ""))]
            case SystemMessage(subtype="compact_boundary", data=data):
                return [compacted(data)]
            case RateLimitEvent(rate_limit_info=info):
                return [rate_limit(info.raw or {"status": info.status, "resetsAt": info.resets_at,
                                                "rateLimitType": info.rate_limit_type, "utilization": info.utilization})]
            case AssistantMessage(content=blocks):
                self.api_error = message.error or self.api_error
                self.started = self.started or not self.injected
                events: list[Event] = []
                for block in blocks:
                    if isinstance(block, TextBlock) and block.text and not self.injected:
                        self.final_text = block.text
                        events.append(Text(text=block.text))
                    elif isinstance(block, ToolUseBlock):
                        events.append(ToolCall(id=block.id, name=block.name, input=block.input or {}))
                return events
            case UserMessage(content=blocks) if isinstance(blocks, list):
                return [ToolResult(id=b.tool_use_id, output=_stringify(b.content), is_error=bool(b.is_error),
                                   images=_images(b.content)) for b in blocks if isinstance(b, ToolResultBlock)]
            case ResultMessage():
                self.done = True
                usage = _usage(message)
                text = message.result or self.final_text
                if self.injected and text:  # the notification was folded into this turn after all: show its reply
                    self.injected = False
                    return [Text(text=text), *self.feed(message, timed_out, timeout_s)]
                if timed_out:
                    return [usage, Done(final_text=text, is_error=True, error=f"timed out after {timeout_s:g}s")]
                failed = message.is_error or message.subtype != "success"
                error = retry = None
                if failed:
                    error = "; ".join(message.errors or []) or message.result or message.subtype or "failed"
                    retry = claude_retry(message.api_error_status, self.api_error)
                return [usage, Done(final_text=text, is_error=failed, error=error, retry=retry)]
        return []


def _usage(message: ResultMessage) -> Usage:
    u = message.usage or {}
    cache_read = u.get("cache_read_input_tokens", 0)
    return Usage(input_tokens=u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + cache_read,
                 output_tokens=u.get("output_tokens", 0), cached_input_tokens=cache_read, cost_usd=message.total_cost_usd,
                 cache_write_tokens=u.get("cache_creation_input_tokens", 0))


def crew_server(crew: "CrewTools | Any") -> Any:
    """The crew tools as an in-process MCP server: each call goes straight to the runner."""

    def make(definition):
        @tool(definition.name, definition.description, definition.schema)
        async def call(args: dict[str, Any]) -> dict[str, Any]:
            try:
                text = await crew.call(definition.name, args)
            except ToolRefused as e:
                return {"content": [{"type": "text", "text": f"crewbots refused this: {e}"}], "is_error": True}
            return {"content": text if isinstance(text, list) else [{"type": "text", "text": text}]}

        return call

    return create_sdk_mcp_server(name=CREW_SERVER, tools=[make(d) for d in crew.definitions()])
