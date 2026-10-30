"""Claude Code, run headless: `claude -p --output-format stream-json`. It uses the CLI's own sign-in, so a Pro/Max plan works."""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from crewbots.backends.base import CLAUDE_BOT_ENV, Access, BackendBase, RunSpec, SessionBase, claude_retry, package_caches
from crewbots.backends.credentials import private_paths, without_credentials
from crewbots.backends.process import JsonlProcess, stream_jsonl
from crewbots.events import Compacted, Done, Event, Notice, RateLimit, SessionStarted, Text, ToolCall, ToolResult, Usage

# ToolSearch loads an MCP tool's definition when it's needed. Left out, Claude Code puts every MCP tool's definition in
# every request instead: four creative-tool servers took a read-only bot's turns from 9K tokens to 91K.
READ_ONLY_TOOLS = "Read,Glob,Grep,ToolSearch"
WEB_TOOLS = "WebFetch,WebSearch"  # with network on; this backend can't check a fetch's host, so a host list is off
EDIT_TOOLS = "Edit,Write,MultiEdit"  # for a read-only bot, only in the folders the user let it write in
# Claude Code's OS sandbox for a writing bot's shell commands: writes kept to the working folder, no opting out.
# Commands may listen on local ports, so a writing bot can run what it's checking, such as a dev server or a scratch
# daemon, and look at it. Without it a bind fails with "Operation not permitted".
SANDBOX = {"enabled": True, "autoAllowBashIfSandboxed": False, "allowUnsandboxedCommands": False,
           "network": {"allowLocalBinding": True}}
# A read-only bot's shell, so it can look things up itself (git diff, a log): the same sandbox, writing nowhere but its
# temp folder (`settings` denies the folders it works in), and running no servers.
READ_SANDBOX = {"enabled": True, "autoAllowBashIfSandboxed": False, "allowUnsandboxedCommands": False}
SANDBOX_TEMP = Path(f"/tmp/claude-{os.getuid()}")  # the sandbox's TMPDIR, where a bot's screenshots and backups go


def shell(access: Access, write_dirs: Sequence[Path] = ()) -> dict[str, Any] | None:
    """The sandbox a bot's shell runs in, or None for no shell (a read-only bot let write in a folder, whose own
    folder can't be denied to its commands without denying that folder too) or no sandbox (full access)."""
    if access is Access.WORKSPACE_WRITE:
        return SANDBOX
    return READ_SANDBOX if access is Access.READ_ONLY and not write_dirs else None


def make_sandbox_temp() -> None:
    """Made before each sandboxed run, since tools needing a temp folder fail while it's missing. Private, as Claude
    Code makes it."""
    SANDBOX_TEMP.mkdir(mode=0o700, exist_ok=True)


def read_rules(paths: Sequence[Path]) -> list[str]:
    """Claude Code's deny rules for these paths, as the files they are or the folders they might be. Its file tools
    obey them, even on full access, and its sandbox keeps commands out of the same paths."""
    return [rule for path in paths for rule in (f"Read(/{path})", f"Read(/{path}/**)")]


# No "Co-Authored-By: Claude" trailer on commits, nor a "Generated with" line on pull requests. `attribution` is the
# current setting and `includeCoAuthoredBy` the deprecated one (Claude Code 2.1.310 reads both); both are set so that
# neither an older nor a newer CLI adds them, whatever the repo's own settings say.
NO_ATTRIBUTION: dict[str, Any] = {"includeCoAuthoredBy": False, "attribution": {"commit": "", "pr": ""}}


def settings(spec: RunSpec) -> dict[str, Any]:
    """What a bot gets on top of the repo's settings. Short of full access: the private paths (backends/credentials.py)
    and `deny_read` denied, and the folders the user let it write in, for this task, allowed to its file tools (and so
    to its sandbox's commands), with a writing bot's package caches. A read-only bot's shell is denied the folders it
    works in (its working folder and those added), which the sandbox would otherwise let commands write in. On full
    access, only `deny_read` (other bots' working state), and only to its file tools: its shell has no sandbox."""
    if spec.access is Access.FULL:
        return {"permissions": {"deny": read_rules(spec.deny_read)}, **NO_ATTRIBUTION} if spec.deny_read else NO_ATTRIBUTION
    permissions: dict[str, list[str]] = {"deny": read_rules([*private_paths(), *spec.deny_read])}
    if shell(spec.access, spec.write_dirs) is READ_SANDBOX:
        works_in = [Path(folder).resolve() for folder in (spec.cwd, *spec.read_dirs)]
        permissions["deny"] += [rule for folder in works_in for rule in (f"Edit(/{folder})", f"Edit(/{folder}/**)")]
    caches = package_caches().values() if spec.access is Access.WORKSPACE_WRITE else ()
    if folders := [*map(str, spec.write_dirs), *caches]:
        permissions["allow"] = [rule for folder in folders for rule in (f"Edit(/{folder})", f"Edit(/{folder}/**)")]
    return {"permissions": permissions, **NO_ATTRIBUTION}


class ClaudeCLI(BackendBase):
    def __init__(self, command: Sequence[str] = ("claude",), use_api_key: bool = False):
        """`use_api_key=False` hides ANTHROPIC_API_KEY from the CLI, so a key left in the environment never
        silently moves a run from the subscription to API billing."""
        self.command = list(command)
        self.use_api_key = use_api_key

    def run(self, spec: RunSpec) -> AsyncIterator[Event]:
        return stream_jsonl(
            self.argv(spec), cwd=spec.cwd, env=self.env(spec), parser=ClaudeParser(), stdin_text=spec.prompt,
            timeout_s=spec.timeout_s, on_prompt_sent=spec.on_prompt_sent,
        )

    async def open(self, spec: RunSpec) -> "ClaudeSession":
        """`claude -p --input-format stream-json` stays running and takes each turn as a message on stdin."""
        argv = [*self.argv(spec), "--input-format", "stream-json"]
        return ClaudeSession(await JsonlProcess.start(argv, cwd=spec.cwd, env=self.env(spec)), spec)

    def env(self, spec: RunSpec) -> dict[str, str]:
        env = dict(os.environ) | CLAUDE_BOT_ENV
        if not self.use_api_key:
            env.pop("ANTHROPIC_API_KEY", None)
        if spec.access is Access.WORKSPACE_WRITE:
            env |= without_credentials() | package_caches()
        elif shell(spec.access, spec.write_dirs):
            env |= without_credentials()
        return env

    def argv(self, spec: RunSpec) -> list[str]:
        args = [
            *self.command,
            "--print",
            "--output-format", "stream-json",
            "--verbose",  # stream-json requires it
            # Only crewbots' own MCP servers, and only the repo's settings: a bot must not inherit the user's
            # personal servers, hooks or plugins.
            "--strict-mcp-config",
            "--mcp-config", json.dumps({"mcpServers": {name: s.config() for name, s in spec.mcp_servers.items()}}),
            "--setting-sources", "project,local",
        ]
        mcp_tools = [f"mcp__{name}" for name in spec.mcp_servers]
        web = [WEB_TOOLS] if spec.network is True else []
        match spec.access:
            case Access.READ_ONLY:
                edits = [EDIT_TOOLS] if spec.write_dirs else []
                bash = ["Bash"] if shell(spec.access, spec.write_dirs) else []
                args += ["--permission-mode", "dontAsk", "--tools", ",".join([READ_ONLY_TOOLS, *bash, *edits, *web])]
                args += ["--allowedTools", ",".join([READ_ONLY_TOOLS, *bash, *web, *mcp_tools])]
            case Access.WORKSPACE_WRITE:
                # acceptEdits allows edits inside the working directory; Bash has to be allowed by name, and runs in
                # the sandbox. With --print there is nobody to ask, so anything else is denied.
                args += ["--permission-mode", "acceptEdits", "--allowedTools", ",".join(["Bash", *web, *mcp_tools])]
            case Access.FULL:
                args += ["--permission-mode", "bypassPermissions"]
        if box := shell(spec.access, spec.write_dirs):
            make_sandbox_temp()
        args += ["--settings", json.dumps(settings(spec) | ({"sandbox": box} if box else {}))]
        for path in [*spec.read_dirs, *spec.write_dirs]:
            args += ["--add-dir", str(path)]
        if spec.model:
            args += ["--model", spec.model]
        if spec.context_window:
            args += ["--autocompact", str(spec.context_window)]
        if spec.system_prompt:
            args += ["--append-system-prompt", spec.system_prompt]
        if spec.resume_session_id:
            args += ["--resume", spec.resume_session_id]
        return args + spec.extra_args


class ClaudeSession(SessionBase):
    def __init__(self, process: JsonlProcess, spec: RunSpec):
        self.process = process
        self.on_prompt_sent = spec.on_prompt_sent
        self.timeout_s = spec.timeout_s
        self.session_id = spec.resume_session_id
        self._broken = False

    @property
    def alive(self) -> bool:
        return self.process.alive and not self._broken

    async def turn(self, prompt: str) -> AsyncIterator[Event]:
        parser = ClaudeParser()
        finished = False
        try:
            await self.process.send({"type": "user", "message": {"role": "user", "content": prompt}})
            if self.on_prompt_sent:
                self.on_prompt_sent()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.timeout_s if self.timeout_s else None
            while True:
                try:
                    obj = await self.process.read(None if deadline is None else max(0.0, deadline - loop.time()))
                except TimeoutError:
                    yield Done(final_text=parser.final_text(), is_error=True, error=f"timed out after {self.timeout_s:g}s")
                    return
                if obj is None:
                    yield Done(final_text=parser.final_text(), is_error=True, error=self.process.exit_detail(),
                               retry="transient")
                    return
                if isinstance(obj, str):
                    yield Notice(message=obj)
                    continue
                for event in parser.feed(obj):
                    if isinstance(event, SessionStarted):
                        self.session_id = event.session_id
                    yield event
                    if isinstance(event, Done):
                        finished = True
                        return
        except (BrokenPipeError, ConnectionResetError):
            yield Done(final_text=parser.final_text(), is_error=True, error=self.process.exit_detail(),
                       retry="transient")
        finally:
            if not finished:
                self._broken = True  # mid-turn, there's no way back to a clean state: stop it
                await self.process.stop()

    async def close(self) -> None:
        self._broken = True
        await self.process.stop()


CLAUDE_LIMIT_STATUS = {"allowed": "ok", "allowed_warning": "warning", "rejected": "limited"}


def compacted(data: dict[str, Any]) -> Compacted:
    """Claude Code's `compact_boundary`, as a crewbots event."""
    meta = data.get("compact_metadata") or {}
    return Compacted(trigger=meta.get("trigger") or "auto", before=meta.get("pre_tokens"), after=meta.get("post_tokens"))


def rate_limit(info: dict[str, Any]) -> RateLimit:
    """Claude's rate_limit_info, which it sends whenever the plan's limit status changes."""
    used = info.get("utilization")
    return RateLimit(plan="claude", window=info.get("rateLimitType") or "plan",
                     status=CLAUDE_LIMIT_STATUS.get(info.get("status", ""), "ok"),
                     used_percent=round(used * 100, 1) if isinstance(used, int | float) else None,
                     resets_at=info.get("resetsAt"))


class ClaudeParser:
    def __init__(self) -> None:
        self._last_text = ""
        self._api_error: str | None = None  # the kind of the last API error Claude Code gave up on, if any

    def final_text(self) -> str:
        return self._last_text

    def feed(self, obj: dict[str, Any]) -> list[Event]:
        if obj.get("parent_tool_use_id"):
            return []  # inside a subagent; its call and result already appear at the top level
        match obj.get("type"):
            case "system" if obj.get("subtype") == "init":
                return [SessionStarted(session_id=obj["session_id"])]
            case "system" if obj.get("subtype") == "compact_boundary":
                return [compacted(obj)]
            case "assistant":
                self._api_error = obj.get("error") or self._api_error
                return self._assistant(obj["message"].get("content") or [])
            case "user":
                content = obj["message"].get("content")
                if not isinstance(content, list):
                    return []
                return [
                    ToolResult(id=b["tool_use_id"], output=_stringify(b.get("content")), is_error=bool(b.get("is_error")),
                               images=_images(b.get("content")))
                    for b in content
                    if b.get("type") == "tool_result"
                ]
            case "result":
                return self._result(obj)
            case "rate_limit_event":
                return [rate_limit(obj.get("rate_limit_info") or {})]
            case _:
                return []  # hook output and so on

    def _assistant(self, blocks: list[dict[str, Any]]) -> list[Event]:
        events: list[Event] = []
        for b in blocks:
            if b.get("type") == "text" and b.get("text"):
                self._last_text = b["text"]
                events.append(Text(text=b["text"]))
            elif b.get("type") == "tool_use":
                events.append(ToolCall(id=b["id"], name=b["name"], input=b.get("input") or {}))
        return events

    def _result(self, obj: dict[str, Any]) -> list[Event]:
        text = obj.get("result") or self._last_text
        return [claude_usage(obj.get("usage"), obj.get("total_cost_usd")), claude_done(text, obj, self._api_error)]


def claude_usage(usage: dict[str, Any] | None, cost: float | None) -> Usage:
    """A Claude result's usage, its input counted whole, cached or not, the way Codex counts it."""
    u = usage or {}
    cache_read, cache_write = u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0)
    return Usage(input_tokens=u.get("input_tokens", 0) + cache_write + cache_read, output_tokens=u.get("output_tokens", 0),
                 cached_input_tokens=cache_read, cost_usd=cost, cache_write_tokens=cache_write)


def claude_done(text: str, result: dict[str, Any], api_error: str | None) -> Done:
    """How Claude Code's result message ends a turn: failed unless it says success, with why, and whether trying again
    would help. `result` is the message as the CLI writes it; the SDK's ResultMessage has the same fields."""
    if not result.get("is_error") and result.get("subtype") == "success":
        return Done(final_text=text)
    error = "; ".join(result.get("errors") or []) or result.get("result") or result.get("subtype") or "failed"
    return Done(final_text=text, is_error=True, error=error, retry=claude_retry(result.get("api_error_status"), api_error))


def _stringify(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if texts or _images(content):  # an image is kept on its own (`_images`), never as text
            return "\n".join(texts)
    return json.dumps(content)


def _images(content: Any) -> list[dict[str, Any]]:
    """The images in a tool's result: a file Read, an MCP tool's screenshot."""
    if not isinstance(content, list):
        return []
    sources = [b.get("source") or {} for b in content if isinstance(b, dict) and b.get("type") == "image"]
    return [{"mime": s.get("media_type") or "image/png", "data": s["data"]} for s in sources
            if s.get("type") == "base64" and s.get("data")]
