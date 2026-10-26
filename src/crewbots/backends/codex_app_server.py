"""A long-lived Codex session over `codex app-server`, Codex's JSON-RPC protocol for clients that stay connected.

One app-server process per session, holding one thread. The role card goes in as the thread's developer
instructions (a real slot, unlike one-shot `exec`, where it has to be prepended to the first prompt), and each turn
is a `turn/start` whose items stream back as notifications until `turn/completed`.
"""

import asyncio
import json
import os
from collections import deque
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from crewbots.backends.base import Access, RunSpec
from crewbots.backends.process import JsonlProcess
from crewbots.events import Done, Event, Notice, SessionStarted, Text, ToolCall, ToolResult, Usage

if TYPE_CHECKING:
    from crewbots.backends.codex_cli import CodexCLI

SANDBOX = {Access.READ_ONLY: "read-only", Access.WORKSPACE_WRITE: "workspace-write", Access.FULL: "danger-full-access"}
OPEN_TIMEOUT_S = 120.0  # starting the thread waits for its MCP servers
CLIENT = {"name": "crewbots", "title": "crewbots", "version": "0.1"}


class AppServerError(RuntimeError):
    pass


class CodexSession:
    def __init__(self, process: JsonlProcess, spec: RunSpec):
        self.process = process
        self.timeout_s = spec.timeout_s
        self.session_id: str | None = None
        self._next_id = 0
        self._pending: deque[dict[str, Any]] = deque()  # notifications read while waiting for a response
        self._announced = False
        self._broken = False

    @classmethod
    async def open(cls, cli: "CodexCLI", spec: RunSpec) -> "CodexSession":
        overrides = await asyncio.to_thread(cli.config_overrides, spec)  # may list Codex's MCP servers, which takes seconds
        argv = [*cli.command, "app-server", "-c", 'approval_policy="never"', *overrides, *spec.extra_args]
        session = cls(await JsonlProcess.start(argv, cwd=spec.cwd, env=dict(os.environ)), spec)
        try:
            await session._request("initialize", {"clientInfo": CLIENT, "capabilities": None})
            await session.process.send({"jsonrpc": "2.0", "method": "initialized"})
            params: dict[str, Any] = {
                "cwd": str(spec.cwd), "approvalPolicy": "never", "sandbox": SANDBOX[spec.access],
                "developerInstructions": spec.system_prompt, "model": spec.model,
            }
            if spec.resume_session_id:
                result = await session._request("thread/resume", {"threadId": spec.resume_session_id, **params,
                                                                  "excludeTurns": True})
            else:
                result = await session._request("thread/start", params)
            session.session_id = result["thread"]["id"]
        except BaseException:
            await session.close()
            raise
        return session

    @property
    def alive(self) -> bool:
        return self.process.alive and not self._broken

    async def turn(self, prompt: str) -> AsyncIterator[Event]:
        parser = AppServerParser()
        finished = False
        try:
            if not self._announced:
                self._announced = True
                yield SessionStarted(session_id=self.session_id or "")
            started = await self._request("turn/start", {
                "threadId": self.session_id, "input": [{"type": "text", "text": prompt, "text_elements": []}],
            })
            turn_id = (started.get("turn") or {}).get("id")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.timeout_s if self.timeout_s else None
            while True:
                try:
                    message = await self._next(None if deadline is None else max(0.0, deadline - loop.time()))
                except TimeoutError:
                    yield Done(final_text=parser.final_text(), is_error=True, error=f"timed out after {self.timeout_s:g}s")
                    return
                if message is None:
                    yield Done(final_text=parser.final_text(), is_error=True, error=self.process.exit_detail())
                    return
                params = message.get("params") or {}
                if params.get("turnId", turn_id) != turn_id and message.get("method") != "turn/completed":
                    continue
                for event in parser.feed(message.get("method", ""), params, turn_id):
                    yield event
                    if isinstance(event, Done):
                        finished = True
                        return
        except (AppServerError, BrokenPipeError, ConnectionResetError) as e:
            yield Done(final_text=parser.final_text(), is_error=True, error=f"codex app-server: {e}")
        finally:
            if not finished:
                self._broken = True
                await self.process.stop()

    async def close(self) -> None:
        self._broken = True
        await self.process.stop()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        await self.process.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while True:
            message = await self._read(OPEN_TIMEOUT_S)
            if message is None:
                raise AppServerError(self.process.exit_detail())
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    raise AppServerError(f"{method}: {message['error'].get('message', message['error'])}")
                return message.get("result") or {}
            self._pending.append(message)

    async def _next(self, timeout_s: float | None) -> dict[str, Any] | None:
        if self._pending:
            return self._pending.popleft()
        return await self._read(timeout_s)

    async def _read(self, timeout_s: float | None) -> dict[str, Any] | None:
        """The next response or notification; None once the server has exited."""
        while True:
            obj = await self.process.read(timeout_s)
            if obj is None:
                return None
            if isinstance(obj, str):
                continue  # stray output that isn't JSON-RPC
            if "method" in obj and "id" in obj:
                # The server asking us something (an approval, user input). Headless, there is nobody to ask.
                await self.process.send({"jsonrpc": "2.0", "id": obj["id"],
                                         "error": {"code": -32601, "message": "crewbots runs headless and can't answer"}})
                continue
            return obj


class AppServerParser:
    """App-server notifications to crewbots events, the same events the one-shot backend gives."""

    def __init__(self) -> None:
        self._last_text = ""
        self._started: set[str] = set()
        self._usage: dict[str, Any] | None = None

    def final_text(self) -> str:
        return self._last_text

    def feed(self, method: str, params: dict[str, Any], turn_id: str | None = None) -> list[Event]:
        match method:
            case "item/started":
                call = _tool_call(params.get("item") or {})
                if call:
                    self._started.add(call.id)
                    return [call]
                return []
            case "item/completed":
                return self._completed(params.get("item") or {})
            case "thread/tokenUsage/updated":
                self._usage = (params.get("tokenUsage") or {}).get("last")
                return []
            case "error":
                error = params.get("error") or {}
                return [Notice(message=error.get("message", "error"))]  # failure itself comes as a failed turn
            case "turn/completed":
                turn = params.get("turn") or {}
                if turn_id and turn.get("id") not in (None, turn_id):
                    return []
                events: list[Event] = []
                if self._usage:
                    u = self._usage
                    events.append(Usage(
                        input_tokens=u.get("inputTokens", 0),
                        output_tokens=u.get("outputTokens", 0) + u.get("reasoningOutputTokens", 0),
                        cached_input_tokens=u.get("cachedInputTokens", 0),
                    ))
                status = turn.get("status")
                if status == "completed":
                    events.append(Done(final_text=self._last_text))
                else:
                    message = (turn.get("error") or {}).get("message") or f"turn {status}"
                    events.append(Done(final_text=self._last_text, is_error=True, error=message))
                return events
            case _:
                return []

    def _completed(self, item: dict[str, Any]) -> list[Event]:
        if item.get("type") == "agentMessage":
            self._last_text = item.get("text", "")
            return [Text(text=self._last_text)] if self._last_text else []
        call = _tool_call(item)
        if not call:
            return []  # user messages, reasoning, plans
        events: list[Event] = [] if call.id in self._started else [call]
        events.append(_tool_result(item))
        return events


def _tool_call(item: dict[str, Any]) -> ToolCall | None:
    match item.get("type"):
        case "commandExecution":
            return ToolCall(id=item["id"], name="shell", input={"command": item.get("command", "")})
        case "fileChange":
            changes = [{"path": c.get("path"), "kind": c.get("kind")} for c in item.get("changes") or []]
            return ToolCall(id=item["id"], name="apply_patch", input={"changes": changes})
        case "mcpToolCall":
            return ToolCall(id=item["id"], name=f"mcp__{item.get('server')}__{item.get('tool')}",
                            input=item.get("arguments") or {})
        case "webSearch":
            return ToolCall(id=item["id"], name="web_search", input={"query": item.get("query", "")})
    return None


def _tool_result(item: dict[str, Any]) -> ToolResult:
    status = item.get("status")
    failed = status in ("failed", "declined")
    match item.get("type"):
        case "commandExecution":
            failed = failed or item.get("exitCode") not in (0, None)
            output = item.get("aggregatedOutput") or ""
        case "mcpToolCall":
            error = item.get("error")
            failed = failed or bool(error)
            output = error.get("message", "") if isinstance(error, dict) else _mcp_text(item.get("result"))
        case _:
            output = status or ""
    return ToolResult(id=item["id"], output=output, is_error=failed)


def _mcp_text(result: Any) -> str:
    if isinstance(result, dict):
        texts = [c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict) and c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return "" if result is None else json.dumps(result)
