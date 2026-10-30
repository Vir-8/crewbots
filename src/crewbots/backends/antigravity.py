"""Antigravity subscriptions through Google's official ACP runtime, one requested turn at a time."""

import asyncio
import contextlib
import json
import shutil
import sys
from collections.abc import AsyncIterator

from crewbots.backends import antigravity_runtime as runtime
from crewbots.backends.acp import Connection
from crewbots.backends.base import Access, McpServer, RunSpec, session_temp_dir
from crewbots.events import Done, Event, SessionStarted, Text, ToolCall, ToolResult, Usage

WORKSPACE = "crewbots_workspace"


def mcp_config(servers: dict[str, McpServer]) -> list[dict]:
    result = []
    for name, server in servers.items():
        if server.transport == "stdio":
            result.append({"name": name, "command": server.command, "args": server.args,
                           "env": [{"name": k, "value": v} for k, v in server.env.items()]})
        else:
            result.append({"name": name, "type": server.transport, "url": server.url,
                           "headers": [{"name": k, "value": v} for k, v in server.headers.items()]})
    return result


class Antigravity:
    automatic_turns = False

    async def open(self, spec: RunSpec) -> "AntigravitySession":
        if spec.proactive:
            raise ValueError("Antigravity is for requested work; proactive turns are disabled")
        if spec.access is Access.FULL:
            raise ValueError("Antigravity supports read-only or workspace-write access, not unrestricted access")
        if WORKSPACE in spec.mcp_servers:
            raise ValueError(f"{WORKSPACE} is reserved for Antigravity's workspace tools")
        if isinstance(spec.network, tuple):
            raise ValueError("Antigravity currently supports network on or off, not individual host lists")
        if sys.platform != "darwin":
            raise ValueError("Antigravity workspace tools currently require macOS")
        connection, info = await runtime.connect(spec.cwd)
        session = AntigravitySession(connection, spec)
        try:
            caps = info.get("agentCapabilities", {})
            for server in spec.mcp_servers.values():
                if server.transport != "stdio" and not caps.get("mcpCapabilities", {}).get(server.transport):
                    raise ValueError(f"This Antigravity runtime doesn't support {server.transport} MCP servers")
            await session.start()
            return session
        except BaseException:
            await session.close()
            raise

    async def run(self, spec: RunSpec) -> AsyncIterator[Event]:
        session = await self.open(spec)
        try:
            async for event in session.turn(spec.prompt):
                yield event
        finally:
            await session.close()


class AntigravitySession:
    def __init__(self, connection: Connection, spec: RunSpec):
        self.connection, self.spec = connection, spec
        self.session_id: str | None = None
        self.temp = session_temp_dir()
        self._broken = False
        self._announced = False
        self._context: float | None = None
        self._calls: dict[str, dict] = {}
        self._text: list[str] = []
        self._last = ""
        connection.handler = self._permission

    @property
    def alive(self) -> bool:
        return not self._broken and self.connection.process.alive

    async def start(self) -> None:
        spec = self.spec
        config = self.temp / "tools.json"
        config.write_text(json.dumps({"cwd": str(spec.cwd.resolve()), "temp": str(self.temp),
            "access": spec.access.value, "network": spec.network,
            "write_dirs": [str(p) for p in spec.write_dirs], "deny_read": [str(p) for p in spec.deny_read]}))
        servers = spec.mcp_servers | {WORKSPACE: McpServer(command=sys.executable,
            args=["-m", "crewbots.backends.antigravity_tools", str(config)])}
        params = {"cwd": str(spec.cwd.resolve()), "mcpServers": mcp_config(servers), "_meta": {"antigravity": {
            "enabledTools": ["finish"], "eagerMcpServers": list(servers),
            "systemPrompt": {"append": (spec.system_prompt or "") + "\nUse crewbots_workspace tools for file reads, "
                "edits, searches and shell commands. They enforce this task's access. Use crew tools for delegation, "
                "questions and memory. Work only on the requested turn; do not create schedules or background agents."},
        }}}
        method = "session/new"
        if spec.resume_session_id:
            params["sessionId"] = spec.resume_session_id
            method = "session/load"
        session = await self.connection.request(method, params, timeout=90)
        self.session_id = session.get("sessionId") or spec.resume_session_id
        if not self.session_id:
            raise RuntimeError("Antigravity did not return a session ID")
        if spec.model:
            if spec.model not in {m["id"] for m in runtime.model_list(session)}:
                raise ValueError(f"Antigravity doesn't offer model {spec.model!r} for this account")
            session = await self.connection.request("session/set_config_option", {
                "sessionId": self.session_id, "configId": "model", "value": spec.model})
        if spec.effort:
            thinking = next((o for o in session.get("configOptions", []) if o.get("id") == "thinking_level"), {})
            if spec.effort not in {o["value"] for o in thinking.get("options", [])}:
                raise ValueError(f"Antigravity doesn't offer effort {spec.effort!r} for this model")
            await self.connection.request("session/set_config_option", {
                "sessionId": self.session_id, "configId": "thinking_level", "value": spec.effort})
        # Loaded history is not a new turn's output.
        while not self.connection.updates.empty():
            self.connection.updates.get_nowait()

    async def _permission(self, method: str, params: dict) -> dict:
        if method != "session/request_permission" or params.get("sessionId") != self.session_id:
            raise ValueError("Unsupported client request")
        call = params.get("toolCall") or {}
        mcp = (call.get("_meta") or {}).get("mcp") or {}
        # Only the servers explicitly passed into this session are granted. Unknown/native actions fail closed.
        allowed = mcp.get("server") in {*self.spec.mcp_servers, WORKSPACE}
        if str(call.get("toolCallId", "")).startswith("interaction_"):
            allowed = False  # Question choices must never be treated as permission approvals.
        kind = "allow_once" if allowed else "reject_once"
        option = next((o for o in params.get("options", []) if o.get("kind") == kind), None)
        return {"outcome": {"outcome": "selected", "optionId": option["optionId"]} if option
                else {"outcome": "cancelled"}}

    def events(self, params: dict) -> list[Event]:
        if params.get("sessionId") != self.session_id:
            return []
        update = params.get("update") or {}
        match update.get("sessionUpdate"):
            case "agent_message_chunk":
                content = update.get("content") or {}
                if content.get("type") == "text":
                    self._text.append(content.get("text", ""))
            case "usage_update":
                if update.get("size"):
                    self._context = 100 * update.get("used", 0) / update["size"]
            case "tool_call" | "tool_call_update":
                key = update["toolCallId"]
                previous = self._calls.get(key)
                call = (previous or {}) | update
                self._calls[key] = call
                mcp = (call.get("_meta") or {}).get("mcp") or {}
                name = (f"mcp__{mcp['server']}__{mcp['tool']}" if mcp.get("server") and mcp.get("tool")
                        else call.get("title", "tool"))
                events: list[Event] = []
                if previous is None:
                    events += self._flush()  # a tool call ends the message before it
                    events.append(ToolCall(id=key, name=name, input=call.get("rawInput") or {}))
                if (call.get("status") in ("completed", "failed")
                        and (previous or {}).get("status") not in ("completed", "failed")):
                    texts, images = [], []
                    for item in call.get("content") or []:
                        block = item.get("content", {}) if item.get("type") == "content" else item
                        if block.get("type") == "text":
                            texts.append(block.get("text", ""))
                        elif block.get("type") == "image" and block.get("data"):
                            images.append({"mime": block.get("mimeType"), "data": block["data"]})
                    output = "\n".join(texts) or json.dumps(call.get("rawOutput", ""))
                    events.append(ToolResult(id=key, output=output, images=images, is_error=call["status"] == "failed"))
                return events
        return []

    def _flush(self) -> list[Event]:
        text = "".join(self._text)
        self._text = []
        if not text:
            return []
        self._last = text
        return [Text(text=text)]

    async def turn(self, prompt: str) -> AsyncIterator[Event]:
        if not self._announced:
            self._announced = True
            yield SessionStarted(session_id=self.session_id or "")
        self._text, self._last, self._calls = [], "", {}
        request = asyncio.create_task(self.connection.request("session/prompt", {
            "sessionId": self.session_id, "prompt": [{"type": "text", "text": prompt}]},
            timeout=self.spec.timeout_s))
        if self.spec.on_prompt_sent:
            self.spec.on_prompt_sent()
        try:
            while not request.done() or not self.connection.updates.empty():
                try:
                    update = await asyncio.wait_for(self.connection.updates.get(), 0.1)
                except TimeoutError:
                    continue
                for event in self.events(update):
                    yield event
            result = await request
            for key, call in self._calls.items():
                if call.get("status") not in ("completed", "failed"):
                    yield ToolResult(id=key, output="The turn ended, but Antigravity ACP did not report this tool's "
                                     "final result. Its success is unverified.", is_error=True)
            usage = result.get("usage")
            if usage:
                cached = usage.get("cachedReadTokens", 0)
                yield Usage(input_tokens=usage.get("inputTokens", 0) + cached, cached_input_tokens=cached,
                            output_tokens=usage.get("outputTokens", 0) + usage.get("thoughtTokens", 0),
                            cache_write_tokens=usage.get("cachedWriteTokens", 0))
            for event in self._flush():
                yield event
            stop = result.get("stopReason")
            error = None if stop == "end_turn" else f"Antigravity stopped: {stop or 'unknown reason'}"
            yield Done(final_text=self._last, is_error=bool(error), error=error)
        except Exception as e:
            self._broken = True
            with contextlib.suppress(Exception):
                await self.connection.notify("session/cancel", {"sessionId": self.session_id})
            error = f"timed out after {self.spec.timeout_s:g}s" if isinstance(e, TimeoutError) else f"Antigravity: {e}"
            yield Done(final_text="".join(self._text) or self._last, is_error=True, error=error)
        finally:
            if not request.done():
                self._broken = True
                with contextlib.suppress(Exception):
                    await self.connection.notify("session/cancel", {"sessionId": self.session_id})
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            if self._broken:
                await self.close()

    async def context_percent(self) -> float | None:
        return self._context

    async def close(self) -> None:
        self._broken = True
        await self.connection.close()
        shutil.rmtree(self.temp, ignore_errors=True)
