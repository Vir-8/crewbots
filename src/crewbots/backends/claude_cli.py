"""Claude Code, run headless: `claude -p --output-format stream-json`. It uses the CLI's own sign-in, so a Pro/Max plan works."""

import json
import os
from collections.abc import AsyncIterator, Sequence
from typing import Any

from crewbots.backends.base import Access, RunSpec, mcp_servers_json
from crewbots.backends.process import stream_jsonl
from crewbots.events import Done, Event, SessionStarted, Text, ToolCall, ToolResult, Usage

READ_ONLY_TOOLS = "Read,Glob,Grep"


class ClaudeCLI:
    name = "claude"

    def __init__(self, command: Sequence[str] = ("claude",), use_api_key: bool = False):
        """`use_api_key=False` hides ANTHROPIC_API_KEY from the CLI, so a key left in the environment never
        silently moves a run from the subscription to API billing."""
        self.command = list(command)
        self.use_api_key = use_api_key

    def run(self, spec: RunSpec) -> AsyncIterator[Event]:
        env = dict(os.environ)
        if not self.use_api_key:
            env.pop("ANTHROPIC_API_KEY", None)
        return stream_jsonl(
            self.argv(spec), cwd=spec.cwd, env=env, parser=ClaudeParser(), stdin_text=spec.prompt,
            timeout_s=spec.timeout_s,
        )

    def argv(self, spec: RunSpec) -> list[str]:
        args = [
            *self.command,
            "--print",
            "--output-format", "stream-json",
            "--verbose",  # stream-json requires it
            # Only crewbots' own MCP servers, and only the repo's settings: a bot must not inherit the user's
            # personal servers, hooks or plugins.
            "--strict-mcp-config",
            "--mcp-config", json.dumps({"mcpServers": mcp_servers_json(spec.mcp_servers)}),
            "--setting-sources", "project,local",
        ]
        mcp_tools = [f"mcp__{name}" for name in spec.mcp_servers]
        match spec.access:
            case Access.READ_ONLY:
                args += ["--permission-mode", "dontAsk", "--tools", READ_ONLY_TOOLS]
                args += ["--allowedTools", ",".join([READ_ONLY_TOOLS, *mcp_tools])]
            case Access.WORKSPACE_WRITE:
                # acceptEdits allows edits inside the working directory; Bash has to be allowed by name. With
                # --print there is nobody to ask, so anything else is denied.
                args += ["--permission-mode", "acceptEdits", "--allowedTools", ",".join(["Bash", *mcp_tools])]
            case Access.FULL:
                args += ["--permission-mode", "bypassPermissions"]
        if spec.model:
            args += ["--model", spec.model]
        if spec.system_prompt:
            args += ["--append-system-prompt", spec.system_prompt]
        if spec.resume_session_id:
            args += ["--resume", spec.resume_session_id]
        return args + spec.extra_args


class ClaudeParser:
    def __init__(self) -> None:
        self._last_text = ""

    def final_text(self) -> str:
        return self._last_text

    def feed(self, obj: dict[str, Any]) -> list[Event]:
        if obj.get("parent_tool_use_id"):
            return []  # inside a subagent; its call and result already appear at the top level
        match obj.get("type"):
            case "system" if obj.get("subtype") == "init":
                return [SessionStarted(session_id=obj["session_id"])]
            case "assistant":
                return self._assistant(obj["message"].get("content") or [])
            case "user":
                content = obj["message"].get("content")
                if not isinstance(content, list):
                    return []
                return [
                    ToolResult(id=b["tool_use_id"], output=_stringify(b.get("content")), is_error=bool(b.get("is_error")))
                    for b in content
                    if b.get("type") == "tool_result"
                ]
            case "result":
                return self._result(obj)
            case _:
                return []  # rate limit notices, hook output and so on

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
        u = obj.get("usage") or {}
        cache_read = u.get("cache_read_input_tokens", 0)
        usage = Usage(
            # Total input, cached or not, the way Codex counts it.
            input_tokens=u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + cache_read,
            output_tokens=u.get("output_tokens", 0),
            cached_input_tokens=cache_read,
            cost_usd=obj.get("total_cost_usd"),
        )
        failed = bool(obj.get("is_error")) or obj.get("subtype") != "success"
        text = obj.get("result") or self._last_text
        error = None
        if failed:
            error = "; ".join(obj.get("errors") or []) or obj.get("result") or obj.get("subtype") or "failed"
        return [usage, Done(final_text=text, is_error=failed, error=error)]


def _stringify(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return json.dumps(content)
