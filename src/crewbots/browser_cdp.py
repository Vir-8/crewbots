"""Multiplexed CDP over a daemon-owned anonymous pipe; no browser TCP listener."""

import asyncio
import contextlib
import json
import logging
from collections import deque
from datetime import UTC, datetime

from crewbots.browser_pipe import OversizedFrame

log = logging.getLogger(__name__)

class BrowserError(Exception):
    pass


class CDP:
    def __init__(self, transport):
        self.transport = transport
        self.pending = {}
        self.sequence = 0
        self.timeout_s = 20
        self.trace = deque(maxlen=200)
        self.paused = {}
        self.commands = {}
        self.event_error = None
        self.on_event = None
        self.on_event_error = None
        self.events: set[asyncio.Task] = set()
        self.reader = asyncio.create_task(self._read())

    async def call(self, method, params=None, session=None):
        self.sequence += 1
        number = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[number] = future
        self.commands[number] = {"method": method, "session": session,
                                 **{k: v for k, v in (params or {}).items() if k in {"url", "requestId", "targetId"}}}
        self.note("send", **self.commands[number])
        try:
            message = {"id": number, "method": method, "params": params or {}}
            if session:
                message["sessionId"] = session
            async with asyncio.timeout(self.timeout_s):
                await self.transport.send(json.dumps(message))
                result = await future
            if method in {"Fetch.continueRequest", "Fetch.failRequest"}:
                self.paused.pop((session, params["requestId"]), None)
            return result
        except TimeoutError:
            detail = json.dumps(self.commands[number])
            raise BrowserError(f"Chrome timed out: {detail}; {self.waiting()}") from None
        finally:
            self.pending.pop(number, None)
            self.commands.pop(number, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # a disconnect may race a failing write before the future is awaited

    async def _read(self):
        try:
            async for raw in self.transport:
                try:
                    self._frame(raw)
                except Exception as e:
                    self.note("malformed_frame", error=str(e))
                    log.warning("Ignoring malformed Chrome CDP frame: %s", e)
        except Exception as e:
            self.note("disconnected", error=str(e))
        finally:
            for future in self.pending.values():
                if not future.done():
                    detail = f"; event handler failed: {self.event_error}" if self.event_error else ""
                    future.set_exception(BrowserError("Chrome disconnected; open the page again" + detail))

    def _frame(self, raw):
        if isinstance(raw, OversizedFrame):
            number = response_id(raw.prefix)
            self.note("oversized_frame", id=number)
            future = self.pending.get(number)
            if future and not future.done():
                future.set_exception(BrowserError("Chrome CDP response exceeds the message size limit"))
            return
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise ValueError("CDP frame is not an object")
        if "id" in message:
            self.note("response", id=message["id"], error=message.get("error"))
            future = self.pending.get(message["id"])
            if future and not future.done():
                if "error" in message:
                    future.set_exception(BrowserError(message["error"].get("message", "CDP error")))
                else:
                    future.set_result(message.get("result", {}))
        elif self.on_event:
            data, session = message.get("params", {}), message.get("sessionId")
            method = message.get("method")
            self.note("event", method=method, session=session,
                      **{k: data[k] for k in ("requestId", "sessionId", "targetInfo") if k in data})
            if method == "Fetch.requestPaused":
                self.paused[(session, data["requestId"])] = data["request"]["url"]
            task = asyncio.create_task(self._dispatch(message))
            self.events.add(task)
            task.add_done_callback(self.events.discard)

    async def _dispatch(self, message):
        try:
            await self.on_event(message)
        except Exception as e:
            self.event_error = str(e)
            data, session = message.get("params", {}), message.get("sessionId")
            self.note("event_error", method=message.get("method"), session=session,
                      requestId=data.get("requestId"), error=str(e))
            if message.get("method") == "Fetch.requestPaused":
                self.paused.pop((session, data.get("requestId")), None)
            # A stale event can race context disposal. Isolate its task if still alive;
            # never disconnect other tasks because one handler failed.
            if self.on_event_error:
                try:
                    await self.on_event_error(message, e)
                except Exception as cleanup:
                    self.note("event_cleanup_error", session=message.get("sessionId"), error=str(cleanup))

    def note(self, kind, **data):
        self.trace.append({"at": datetime.now(UTC).isoformat(), "kind": kind, **data})

    def fail_session(self, session, error):
        for number, command in list(self.commands.items()):
            future = self.pending.get(number)
            if command["session"] == session and future and not future.done():
                future.set_exception(BrowserError(error))

    def waiting(self) -> str:
        requests = [f"{request}: {url} (session {session})" for (session, request), url in self.paused.items()]
        return "paused requests: " + ("; ".join(requests) if requests else "none")

    def diagnostics(self) -> dict:
        return {"trace": list(self.trace), "commands": list(self.commands.values()),
                "paused_requests": self.waiting(), "event_error": self.event_error}

    async def close(self):
        await self.transport.close()
        self.reader.cancel()
        events = [task for task in self.events if task is not asyncio.current_task()]
        for task in events:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.reader
        await asyncio.gather(*events, return_exceptions=True)


def response_id(prefix: bytes) -> int | None:
    """Read only a top-level id from a bounded JSON prefix, never an id nested in page data."""
    text = prefix.decode("utf-8", errors="replace")
    decoder = json.JSONDecoder()
    index = 0

    def skip_space(position):
        while position < len(text) and text[position] in " \t\r\n":
            position += 1
        return position

    try:
        index = skip_space(index)
        if text[index] != "{":
            return None
        index += 1
        while True:
            key, index = decoder.raw_decode(text, skip_space(index))
            if not isinstance(key, str):
                return None
            index = skip_space(index)
            if text[index] != ":":
                return None
            value, index = decoder.raw_decode(text, skip_space(index + 1))
            if key == "id":
                end = skip_space(index)
                return value if type(value) is int and end < len(text) and text[end] in ",}" else None
            index = skip_space(index)
            if text[index] != ",":
                return None
            index += 1
    except (ValueError, IndexError, RecursionError):
        return None
