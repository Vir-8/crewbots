"""Claude through the Claude Agent SDK, the official way to drive Claude Code from a program.

The SDK still runs the `claude` binary (the user's own, signed in, so a Pro/Max plan works), but over a control
protocol a plain `claude -p` doesn't have, which gives crewbots three things:

- **Interrupt.** A turn can be stopped (cancel, timeout) without killing the process, so the session stays warm.
- **In-process crew tools.** crewbots' tools run in the daemon as an SDK MCP server: no subprocess, no token.
- **A say on every tool call.** `can_use_tool` applies crewbots' policy (`Policy`) instead of a fixed allowlist.
"""

import asyncio
import contextlib
import os
import re
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from crewbots.backends.base import Access, RunSpec
from crewbots.backends.claude_cli import READ_ONLY_TOOLS, _stringify
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done, Event, Notice, SessionStarted, Text, ToolCall, ToolResult, Usage

CREW_SERVER = "crewbots"
INTERRUPT_GRACE_S = 20.0  # after an interrupt, how long the turn may take to wind down before the session is dropped
READ_TOOLS = {"Read", "Glob", "Grep", "LS", "NotebookRead", "TodoWrite"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# The user's to do, never a bot's (team/rules.md): sending work out of the crew, or rewriting what's there.
# It's a guardrail, not a boundary: a determined command can always get around a pattern (containers are the real one).
OUTWARD = re.compile(
    r"\bgit\b(?:\s+-[Cc]\s+\S+|\s+--?[\w-]+(?:=\S+)?)*\s+(push|merge|rebase|reset\s+--hard|branch\s+-D|tag)\b"
    r"|\bgh\s+(pr\s+(create|merge)|release|repo\s+delete)\b"
    r"|\b(npm|pnpm|yarn|cargo|poetry|uv)\s+publish\b|\btwine\s+upload\b"
)
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
        servers: dict[str, Any] = {
            name: {"type": "stdio", "command": s.command, "args": s.args, "env": s.env}
            for name, s in spec.mcp_servers.items() if name != CREW_SERVER
        }
        if isinstance(spec.crew, CrewTools):
            servers[CREW_SERVER] = crew_server(spec.crew)
        full = spec.access is Access.FULL
        env = {} if self.use_api_key or "ANTHROPIC_API_KEY" not in os.environ else {"ANTHROPIC_API_KEY": ""}
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
            add_dirs=[str(d) for d in spec.read_dirs],
            tools=READ_ONLY_TOOLS.split(",") if spec.access is Access.READ_ONLY else None,
            permission_mode="bypassPermissions" if full else "default",
            can_use_tool=None if full else session.policy,
            env=env,
            stderr=session.stderr_lines.append,
        )


class ClaudeAgentSession:
    interruptible = True

    def __init__(self, spec: RunSpec):
        self.client: ClaudeSDKClient | None = None
        self.session_id = spec.resume_session_id
        self.timeout_s = spec.timeout_s
        self.policy = Policy(spec.access, Path(spec.cwd), set(spec.mcp_servers) | {CREW_SERVER})
        self.stderr_lines: list[str] = []
        self._broken = False

    @property
    def alive(self) -> bool:
        return self.client is not None and not self._broken

    async def turn(self, prompt: str) -> AsyncIterator[Event]:
        assert self.client
        mapper = SdkMapper()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        settled = False

        async def pump() -> None:
            try:
                async for message in self.client.receive_response():  # until this turn's ResultMessage
                    await queue.put(message)
            except Exception as e:  # the CLI died, or the protocol broke
                await queue.put(e)
            finally:
                await queue.put(_END)

        try:
            await self.client.query(prompt)
        except Exception as e:
            self._broken = True
            yield Done(final_text="", is_error=True, error=f"claude: {e}")
            return
        reader = asyncio.create_task(pump())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_s if self.timeout_s else None
        timed_out = False
        try:
            while True:
                for notice in self.policy.take_refusals():
                    yield Notice(message=notice)
                remaining = None if deadline is None else deadline - loop.time()
                try:
                    message = await asyncio.wait_for(queue.get(), None if remaining is None else max(0.0, remaining))
                except TimeoutError:
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
                        yield Done(final_text=mapper.final_text, is_error=True, error=f"claude: {detail}")
                    return
                if isinstance(message, Exception):
                    self._broken = True
                    yield Done(final_text=mapper.final_text, is_error=True, error=f"claude: {message}")
                    settled = True
                    return
                for event in mapper.feed(message, timed_out=timed_out, timeout_s=self.timeout_s):
                    if isinstance(event, SessionStarted):
                        self.session_id = event.session_id
                    yield event
        finally:
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


class Policy:
    """crewbots' say on Claude's tool calls: what each access level may do, wherever the tool call comes from."""

    def __init__(self, access: Access, cwd: Path, granted_servers: set[str]):
        self.access = access
        self.cwd = cwd.resolve()
        self.granted = granted_servers
        self._refusals: list[str] = []

    async def __call__(self, name: str, args: dict[str, Any], context: ToolPermissionContext):
        reason = self.refuse(name, args)
        if reason is None:
            return PermissionResultAllow()
        self._refusals.append(f"Refused {name}: {reason}")
        return PermissionResultDeny(message=reason)

    def refuse(self, name: str, args: dict[str, Any]) -> str | None:
        """None to allow the call, or why not."""
        if name.startswith("mcp__"):
            server = name.split("__")[1] if name.count("__") >= 2 else ""
            return None if server in self.granted else f"the MCP server {server!r} isn't granted by this bot's role card"
        if name in READ_TOOLS:
            return None
        if self.access is Access.READ_ONLY:
            return "this bot is read-only (its role card says access: read_only)"
        if name in EDIT_TOOLS:
            target = args.get("file_path") or args.get("notebook_path") or ""
            if not target or not self._inside(target):
                return f"writes are limited to this task's own folder, {self.cwd}"
            return None
        if name == "Bash" and OUTWARD.search(str(args.get("command", ""))):
            return "pushing, merging, publishing and rewriting history are the user's to do (team/rules.md)"
        return None

    def take_refusals(self) -> list[str]:
        out, self._refusals = self._refusals, []
        return out

    def _inside(self, path: str) -> bool:
        target = Path(path) if os.path.isabs(path) else self.cwd / path
        try:
            return target.resolve().is_relative_to(self.cwd)
        except OSError:
            return False


class SdkMapper:
    """Agent SDK messages to crewbots events, the same ones every backend gives."""

    def __init__(self) -> None:
        self.final_text = ""
        self.done = False

    def feed(self, message: Any, timed_out: bool = False, timeout_s: float | None = None) -> list[Event]:
        if getattr(message, "parent_tool_use_id", None):
            return []  # inside a subagent; its call and result already appear at the top level
        match message:
            case SystemMessage(subtype="init", data=data):
                return [SessionStarted(session_id=data.get("session_id", ""))]
            case AssistantMessage(content=blocks):
                events: list[Event] = []
                for block in blocks:
                    if isinstance(block, TextBlock) and block.text:
                        self.final_text = block.text
                        events.append(Text(text=block.text))
                    elif isinstance(block, ToolUseBlock):
                        events.append(ToolCall(id=block.id, name=block.name, input=block.input or {}))
                return events
            case UserMessage(content=blocks) if isinstance(blocks, list):
                return [ToolResult(id=b.tool_use_id, output=_stringify(b.content), is_error=bool(b.is_error))
                        for b in blocks if isinstance(b, ToolResultBlock)]
            case ResultMessage():
                self.done = True
                u = message.usage or {}
                cache_read = u.get("cache_read_input_tokens", 0)
                usage = Usage(
                    input_tokens=u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + cache_read,
                    output_tokens=u.get("output_tokens", 0), cached_input_tokens=cache_read,
                    cost_usd=message.total_cost_usd,
                )
                text = message.result or self.final_text
                if timed_out:
                    return [usage, Done(final_text=text, is_error=True, error=f"timed out after {timeout_s:g}s")]
                failed = message.is_error or message.subtype != "success"
                error = None
                if failed:
                    error = "; ".join(message.errors or []) or message.result or message.subtype or "failed"
                return [usage, Done(final_text=text, is_error=failed, error=error)]
        return []


def crew_server(crew: CrewTools) -> Any:
    """The crew tools as an in-process MCP server: each call goes straight to the runner."""

    def make(definition):
        @tool(definition.name, definition.description, definition.schema)
        async def call(args: dict[str, Any]) -> dict[str, Any]:
            try:
                text = await crew.call(definition.name, args)
            except ToolRefused as e:
                return {"content": [{"type": "text", "text": f"crewbots refused this: {e}"}], "is_error": True}
            return {"content": [{"type": "text", "text": text}]}

        return call

    return create_sdk_mcp_server(name=CREW_SERVER, tools=[make(d) for d in crew.definitions()])
