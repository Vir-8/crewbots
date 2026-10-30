"""The ACP stdio connection. The agent owns model requests and authentication."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from crewbots.backends.process import JsonlProcess


class ACPError(RuntimeError):
    def __init__(self, error: dict[str, Any]):
        detail = error.get("data") or {}
        message = detail.get("message") if isinstance(detail, dict) else None
        super().__init__(message or error.get("message", "ACP request failed"))


class Connection:
    def __init__(self, process: JsonlProcess):
        self.process = process
        self.pending: dict[int, asyncio.Future] = {}
        self.next_id = 0
        self.updates: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.handler: Callable[[str, dict], Awaitable[dict]] | None = None
        self.handlers: set[asyncio.Task] = set()
        self.reader = asyncio.create_task(self._read())

    async def request(self, method: str, params: dict, timeout: float | None = 30) -> dict:
        self.next_id += 1
        request_id = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.process.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def notify(self, method: str, params: dict) -> None:
        await self.process.send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _answer(self, message: dict) -> None:
        answer = {"jsonrpc": "2.0", "id": message["id"]}
        try:
            if self.handler is None:
                raise ValueError("This client cannot handle that request")
            answer["result"] = await self.handler(message["method"], message.get("params") or {})
        except Exception:
            answer["error"] = {"code": -32601, "message": "Request not permitted by crewbots"}
        try:
            await self.process.send(answer)
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def _read(self) -> None:
        try:
            while True:
                message = await self.process.read()
                if message is None:
                    break
                if not isinstance(message, dict):
                    continue
                if "method" in message:
                    if "id" in message:
                        task = asyncio.create_task(self._answer(message))
                        self.handlers.add(task)
                        task.add_done_callback(self.handlers.discard)
                    elif message["method"] == "session/update":
                        await self.updates.put(message.get("params") or {})
                elif (future := self.pending.get(message.get("id"))) is not None and not future.done():
                    if "error" in message:
                        future.set_exception(ACPError(message["error"]))
                    else:
                        future.set_result(message.get("result") or {})
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("ACP agent disconnected"))

    async def close(self) -> None:
        await self.process.stop()
        self.reader.cancel()
        for task in self.handlers:
            task.cancel()
        await asyncio.gather(self.reader, *self.handlers, return_exceptions=True)
