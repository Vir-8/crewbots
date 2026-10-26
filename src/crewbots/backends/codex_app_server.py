"""A long-lived Codex session over `codex app-server`, Codex's JSON-RPC protocol for clients that stay connected.

One app-server process per session, holding one thread. The role card goes in as the thread's developer
instructions (a real slot, unlike one-shot `exec`, where it has to be prepended to the first prompt), and each turn
is a `turn/start` whose items stream back as notifications until `turn/completed`.

The sandbox is the boundary. Commands run inside Codex's sandbox (writes only in the task's folder and a private
temp folder, no network) without asking. Codex asks only when it wants out of the sandbox, because accepting an
approval runs that command *unsandboxed*. So every escalation is declined, with the reason from crewbots' policy
(backends/policy.py), and file changes Codex asks about are checked against it. A refusal is steered into the turn
with its reason, since a bare "decline" doesn't tell the model why. With full access there's no sandbox to leave.
"""

import asyncio
import contextlib
import json
import os
import re
import shutil
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crewbots.backends.base import Access, RunSpec, session_temp_dir
from crewbots.backends.policy import NO_NETWORK, STAY_IN_SANDBOX, Policy
from crewbots.backends.process import JsonlProcess
from crewbots.events import Done, Event, Notice, SessionStarted, Text, ToolCall, ToolResult, Usage

if TYPE_CHECKING:
    from crewbots.backends.codex_cli import CodexCLI

SANDBOX = {Access.READ_ONLY: "read-only", Access.WORKSPACE_WRITE: "workspace-write", Access.FULL: "danger-full-access"}
OPEN_TIMEOUT_S = 120.0  # starting the thread waits for its MCP servers
CLIENT = {"name": "crewbots", "title": "crewbots", "version": "0.1"}
CREW_SERVER = "crewbots"


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
        full = spec.access is Access.FULL
        self.approval_policy = "never" if full else "on-request"
        self.policy = None if full else Policy(spec.access, spec.cwd, set(spec.mcp_servers) | {CREW_SERVER})
        self._files: dict[str, list[str]] = {}  # file change item id → the paths it touches, for its approval
        self._turn_id: str | None = None
        self._steers: list[str] = []  # reasons to tell the model, after a refusal
        self.temp: Path | None = None

    @classmethod
    async def open(cls, cli: "CodexCLI", spec: RunSpec) -> "CodexSession":
        overrides = await asyncio.to_thread(cli.config_overrides, spec)  # may list Codex's MCP servers, which takes seconds
        policy = "never" if spec.access is Access.FULL else "on-request"
        env = dict(os.environ)
        temp = None
        if spec.access is Access.WORKSPACE_WRITE:
            # The sandbox otherwise lets commands write to /tmp and $TMPDIR too: a way out of the task's folder.
            temp = session_temp_dir()
            env["TMPDIR"] = str(temp)
            overrides += ["-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                          "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                          "-c", f"sandbox_workspace_write.writable_roots={json.dumps([str(temp)])}"]
        argv = [*cli.command, "app-server", "-c", f'approval_policy="{policy}"', *overrides, *spec.extra_args]
        session = cls(await JsonlProcess.start(argv, cwd=spec.cwd, env=env), spec)
        session.temp = temp
        try:
            await session._request("initialize", {"clientInfo": CLIENT, "capabilities": None})
            await session.process.send({"jsonrpc": "2.0", "method": "initialized"})
            params: dict[str, Any] = {
                "cwd": str(spec.cwd), "approvalPolicy": session.approval_policy, "sandbox": SANDBOX[spec.access],
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
            self._turn_id = turn_id
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.timeout_s if self.timeout_s else None
            while True:
                for notice in self.policy.take_refusals() if self.policy else []:
                    yield Notice(message=notice)
                await self._steer()
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
                    if isinstance(event, Done):
                        for notice in self.policy.take_refusals() if self.policy else []:
                            yield Notice(message=notice)
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
        if self.temp:
            shutil.rmtree(self.temp, ignore_errors=True)

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
        """The next response or notification; None once the server has exited. Approval requests are answered here."""
        while True:
            obj = await self.process.read(timeout_s)
            if obj is None:
                return None
            if isinstance(obj, str):
                continue  # stray output that isn't JSON-RPC
            if obj.get("method") in ("item/started", "item/completed"):
                item = (obj.get("params") or {}).get("item") or {}
                if item.get("type") == "fileChange":
                    self._files[item["id"]] = [c.get("path", "") for c in item.get("changes") or []]
            if "method" in obj and "id" in obj:
                answer = self._answer(obj["method"], obj.get("params") or {})
                await self.process.send({"jsonrpc": "2.0", "id": obj["id"], **answer})
                continue
            return obj

    def _answer(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """The reply to a request from Codex: an approval decided by crewbots' policy, or a refusal to anything else."""
        reason: str | None
        match method:
            case "item/commandExecution/requestApproval":
                command = _unwrap(params.get("command") or "")
                if params.get("networkApprovalContext"):
                    host = params["networkApprovalContext"].get("host", "the network")
                    reason, what = NO_NETWORK, f"network access to {host}"
                else:
                    # Accepting would run it outside the sandbox. Say why, in the policy's words where they apply.
                    rule = self.policy.check_command(command, sandboxed=True) if self.policy else None
                    reason, what = rule or STAY_IN_SANDBOX, command
                return {"result": {"decision": self._decide(what, reason)}}
            case "item/fileChange/requestApproval":
                paths = self._files.get(params.get("itemId", ""), [])
                reason = next((r for p in paths if self.policy and (r := self.policy.check_write(p))), None)
                if self.policy and not paths:
                    reason = "crewbots couldn't see which files this change touches"
                return {"result": {"decision": self._decide(", ".join(paths) or "a file change", reason)}}
            case "execCommandApproval" | "applyPatchApproval":  # the older protocol, should it ever be used
                what = " ".join(params.get("command") or []) or ", ".join(params.get("fileChanges") or {}) or method
                reason = "crewbots answers the current approval protocol only"
                self._decide(what, reason)
                return {"result": {"decision": {"denied": {"rejection": reason}}}}
        return {"error": {"code": -32601, "message": f"crewbots runs headless and can't answer {method}"}}

    def _decide(self, what: str, reason: str | None) -> str:
        if reason is None:
            return "accept"
        if self.policy:
            self.policy.refused(what, reason)
        self._steers.append(f"crewbots declined {what}: {reason}. Don't retry it another way; work within this, or "
                            "use the escalate tool if the task can't be done without it.")
        return "decline"

    async def _steer(self) -> None:
        """Tells the model why something was declined, as input to the turn in progress."""
        while self._steers and self._turn_id:
            text = self._steers.pop(0)
            with contextlib.suppress(AppServerError):
                await self._request("turn/steer", {"threadId": self.session_id, "expectedTurnId": self._turn_id,
                                                   "input": [{"type": "text", "text": text, "text_elements": []}]})


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


def _unwrap(command: str) -> str:
    """Codex runs commands as /bin/zsh -lc '…'; the command itself is what matters, and what people read."""
    match = re.fullmatch(r"/bin/(?:ba|z)?sh -lc (['\"])([\s\S]*)\1", command.strip())
    return match.group(2) if match else command
