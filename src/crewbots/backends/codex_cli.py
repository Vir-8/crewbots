"""Codex, on its own sign-in, so a ChatGPT plan works.

`CodexCLI` (the `codex` backend) runs every turn through `codex app-server`, in a session that stays open between
turns or one opened for a single turn. Either way crewbots' policy answers Codex's approval requests. `CodexExec` (the
`codex-exec` backend) is the older one-shot `codex exec --json`: it can't be asked for approvals, so it has Codex's
sandbox and crewbots' isolation, but not crewbots' policy.
"""

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crewbots.backends.base import Access, McpServer, RunSpec, package_caches
from crewbots.backends.codex_isolation import isolation_overrides
from crewbots.backends.credentials import private_paths, without_credentials
from crewbots.backends.process import stream_jsonl
from crewbots.events import Done, Event, Notice, SessionStarted, Text, ToolCall, ToolResult, Usage

if TYPE_CHECKING:
    from crewbots.backends.codex_app_server import CodexSession

MCP_NAME = re.compile(r"[A-Za-z0-9_-]+")
PROFILE = "crewbots"
BASE_PROFILE = {Access.READ_ONLY: ":read-only", Access.WORKSPACE_WRITE: ":workspace"}


class CodexExec:
    name = "codex-exec"

    def __init__(self, command: Sequence[str] = ("codex",), isolate: bool = True):
        """`isolate=False` lets the bot use the user's own Codex MCP servers and plugins; only for trying things out."""
        self.command = list(command)
        self.isolate = isolate

    async def run(self, spec: RunSpec) -> AsyncIterator[Event]:
        argv = await asyncio.to_thread(self.argv, spec)  # may list Codex's MCP servers, which takes seconds
        env = dict(os.environ) | (without_credentials() | package_caches() if spec.access is Access.WORKSPACE_WRITE
                                  else {})
        async with aclosing(stream_jsonl(argv, cwd=spec.cwd, env=env, parser=CodexParser(),
                                         stdin_text=self.prompt(spec), timeout_s=spec.timeout_s)) as stream:
            async for event in stream:
                yield event

    async def open(self, spec: RunSpec) -> "CodexSession":
        from crewbots.backends.codex_app_server import CodexSession

        return await CodexSession.open(self, spec)

    def argv(self, spec: RunSpec) -> list[str]:
        # Codex's sandboxes limit writes, not reads, so `read_dirs` needs nothing here.
        # `exec resume` takes neither -s nor -C, so the sandbox goes through -c and the directory is the process's cwd.
        args = [*self.command, "exec"]
        if spec.resume_session_id:
            args += ["resume", spec.resume_session_id]
        args += ["--json", "--skip-git-repo-check", "-c", 'approval_policy="never"']
        if spec.access is Access.FULL:
            args.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            args += [*sandbox_overrides(spec), *network_overrides(spec)]
        args += self.config_overrides(spec)
        if spec.model:
            args += ["--model", spec.model]
        if spec.effort:
            args += ["-c", f'model_reasoning_effort="{spec.effort}"']
        return [*args, *spec.extra_args, "-"]  # "-": the prompt comes on stdin

    def config_overrides(self, spec: RunSpec) -> list[str]:
        """The user's servers, plugins and apps switched off, then the bot's own servers added. Codex's own memories
        stay off whatever the user's config says: a bot's notes are crewbots'."""
        args = ["-c", "features.memories=false"]
        if spec.context_window:
            args += ["-c", f"model_auto_compact_token_limit={compact_limit(spec.context_window)}"]
        args += isolation_overrides(self.command, keep=spec.mcp_servers) if self.isolate else []
        for name, server in spec.mcp_servers.items():
            args += _mcp_overrides(name, server)
        return args

    def prompt(self, spec: RunSpec) -> str:
        # Codex has no flag for adding to its system prompt. On a resumed session the instructions are already in
        # the conversation, so they are only sent with the first prompt.
        if spec.system_prompt and not spec.resume_session_id:
            return f"<instructions>\n{spec.system_prompt}\n</instructions>\n\n{spec.prompt}"
        return spec.prompt


class CodexCLI(CodexExec):
    name = "codex"

    async def run(self, spec: RunSpec) -> AsyncIterator[Event]:
        """One turn on its own: a session opened for it and closed after, so the policy still answers approvals."""
        session = await self.open(spec)
        try:
            async with aclosing(session.turn(spec.prompt)) as turn:
                async for event in turn:
                    yield event
        finally:
            await session.close()


class CodexParser:
    def __init__(self) -> None:
        self._last_text = ""
        self._started: set[str] = set()

    def final_text(self) -> str:
        return self._last_text

    def feed(self, obj: dict[str, Any]) -> list[Event]:
        match obj.get("type"):
            case "thread.started":
                return [SessionStarted(session_id=obj["thread_id"])]
            case "item.started":
                call = _tool_call(obj["item"])
                if call:
                    self._started.add(call.id)
                    return [call]
                return []
            case "item.completed":
                return self._completed(obj["item"])
            case "turn.completed":
                u = obj.get("usage") or {}
                usage = Usage(
                    input_tokens=u.get("input_tokens", 0),
                    output_tokens=u.get("output_tokens", 0) + u.get("reasoning_output_tokens", 0),
                    cached_input_tokens=u.get("cached_input_tokens", 0),
                )
                return [usage, Done(final_text=self._last_text)]
            case "turn.failed":
                message = (obj.get("error") or {}).get("message") or "turn failed"
                return [Done(final_text=self._last_text, is_error=True, error=message)]
            case "error":
                return [Notice(message=obj.get("message", ""))]  # reconnects and retries; a real failure is turn.failed
            case _:
                return []

    def _completed(self, item: dict[str, Any]) -> list[Event]:
        if item.get("type") == "agent_message":
            self._last_text = item.get("text", "")
            return [Text(text=self._last_text)]
        if item.get("type") == "error":
            return [Notice(message=item.get("message", ""))]
        call = _tool_call(item)
        if not call:
            return []  # reasoning, todo lists
        events: list[Event] = [] if call.id in self._started else [call]  # file changes only arrive completed
        events.append(_tool_result(item))
        return events


def _tool_call(item: dict[str, Any]) -> ToolCall | None:
    match item.get("type"):
        case "command_execution":
            return ToolCall(id=item["id"], name="shell", input={"command": item.get("command", "")})
        case "file_change":
            return ToolCall(id=item["id"], name="apply_patch", input={"changes": item.get("changes", [])})
        case "mcp_tool_call":
            name = f"mcp__{item.get('server')}__{item.get('tool')}"  # the same naming Claude uses
            return ToolCall(id=item["id"], name=name, input=item.get("arguments") or {})
        case "web_search":
            return ToolCall(id=item["id"], name="web_search", input={"query": item.get("query", "")})
    return None


def _tool_result(item: dict[str, Any]) -> ToolResult:
    status = item.get("status")
    failed = status in ("failed", "declined")
    match item.get("type"):
        case "command_execution":
            failed = failed or item.get("exit_code") not in (0, None)
            output = item.get("aggregated_output", "")
        case "mcp_tool_call":
            error = item.get("error")
            failed = failed or bool(error)
            output = error.get("message", "") if isinstance(error, dict) else _mcp_text(item.get("result"))
        case _:
            output = status or ""
    return ToolResult(id=item["id"], output=output, is_error=failed)


def _mcp_text(result: Any) -> str:
    if isinstance(result, dict):
        texts = [c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return "" if result is None else json.dumps(result)


def _mcp_overrides(name: str, server: McpServer) -> list[str]:
    """`-c` overrides in TOML. A JSON string or list of strings is also valid TOML; an inline table is not."""
    if not MCP_NAME.fullmatch(name):
        raise ValueError(f"MCP server name {name!r} must be letters, digits, - or _ (it becomes a config key)")
    key = f"mcp_servers.{name}"
    # A headless run has nobody to approve a call, so a tool needing approval would always be refused ("requires
    # approval, but approval policy is never"). The role card granting the server is the approval, the same as
    # Claude's --allowedTools.
    args = ["-c", f'{key}.default_tools_approval_mode="approve"']
    if server.transport == "sse":
        raise ValueError(f"MCP server {name!r} uses SSE, which Codex can't; run this bot on Claude, or use the "
                         "server's streamable HTTP address")
    if server.transport == "http":
        args += ["-c", f"{key}.url={json.dumps(server.url)}"]
        if server.headers:
            args += ["-c", f"{key}.http_headers={_toml_table(server.headers)}"]
        return args
    args += ["-c", f"{key}.command={json.dumps(server.command)}", "-c", f"{key}.args={json.dumps(server.args)}"]
    if server.env:
        args += ["-c", f"{key}.env={_toml_table(server.env)}"]
    return args


def _toml_table(values: dict[str, str]) -> str:
    return "{" + ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in values.items()) + "}"


def compact_limit(window: int) -> int:
    """Where Codex compacts within a bot's window. Codex compacts at a token count rather than taking a window, so
    this leaves about the room Claude Code keeps at the end of the same window (167K of 200K)."""
    return int(window * 0.85)


def sandbox_overrides(spec: RunSpec, temp: Path | None = None) -> list[str]:
    """Codex's sandbox for a bot that isn't on full access, as a permissions profile of crewbots' own: Codex's built-in
    one for the bot's access, with the private paths (backends/credentials.py) unreadable, and the network on only for
    a writing bot whose role card turns it on. `temp`, a writing bot's own temp folder, takes the place of /tmp and
    $TMPDIR, which commands could otherwise write to: a way out of the task's folder. The folders the user let it
    write in, for this task, are writable too, and a writing bot's package caches. (Codex's older sandbox settings can
    only limit writes, and a profile set here wins over them, even with `sandbox_mode` in the user's config.)"""
    files = {str(path): "deny" for path in private_paths()}
    if temp is not None:
        files |= {":slash_tmp": "read", ":tmpdir": "read", str(temp): "write"}
    files |= {str(folder): "write" for folder in spec.write_dirs}
    if spec.access is Access.WORKSPACE_WRITE:
        files |= {folder: "write" for folder in package_caches().values()}
    files |= {str(path): "deny" for path in spec.deny_read}
    table = ", ".join(f"{json.dumps(key)} = {json.dumps(access)}" for key, access in files.items())
    network = spec.network is True and spec.access is Access.WORKSPACE_WRITE
    return ["-c", f'default_permissions="{PROFILE}"',
            "-c", f'permissions.{PROFILE}.extends="{BASE_PROFILE[spec.access]}"',
            "-c", f"permissions.{PROFILE}.filesystem={{{table}}}",
            "-c", f"permissions.{PROFILE}.network.enabled={str(network).lower()}"]


def network_overrides(spec: RunSpec) -> list[str]:
    """Codex's sandbox has the network on or off for the whole run (sandbox_overrides); it can't keep to a list of
    hosts. With it off, so is its web search, which runs on OpenAI's side, past the sandbox."""
    if isinstance(spec.network, tuple) and spec.network:
        raise ValueError("Codex can't limit the network to some hosts; give this bot network: true, or run it on Claude")
    if not spec.network and spec.access is not Access.FULL:
        return ['-c', 'web_search="disabled"']
    return []
