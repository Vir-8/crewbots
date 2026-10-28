"""Chrome's NUL-delimited CDP pipe. These anonymous descriptors are never a TCP service."""

import asyncio
import contextlib
import os
from collections import deque
from dataclasses import dataclass

LIMIT = 32 * 1024 * 1024
PREFIX = 64 * 1024  # retain only enough of an oversized response to identify its command
WRITE_TIMEOUT_S = 1.0
CLOSE_TIMEOUT_S = 1.0


@dataclass(frozen=True)
class OversizedFrame:
    prefix: bytes


class WriteProtocol(asyncio.streams.FlowControlMixin):
    def __init__(self, loop):
        super().__init__(loop=loop)
        self.closed = loop.create_future()

    def connection_lost(self, exc):
        super().connection_lost(exc)
        if not self.closed.done():
            self.closed.set_result(None)

    def _get_close_waiter(self, stream):
        return self.closed


class Pipe:
    def __init__(self, reader, writer, transport, limit):
        self.reader = reader
        self.writer = writer
        self.transport = transport
        self.limit = limit
        self._frame = bytearray()
        self._oversized = None
        self._ready = deque()
        self._send_lock = asyncio.Lock()
        self._aborted = False

    @classmethod
    async def open(cls, read_fd: int, write_fd: int):
        """Take ownership of both descriptors, including on any setup failure."""
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=LIMIT)
        read_file = write_file = transport = None
        try:
            read_file = os.fdopen(read_fd, "rb", buffering=0)
            read_fd = -1
            write_file = os.fdopen(write_fd, "wb", buffering=0)
            write_fd = -1
            transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), read_file)
            protocol = WriteProtocol(loop)
            output, _ = await loop.connect_write_pipe(lambda: protocol, write_file)
            return cls(reader, asyncio.StreamWriter(output, protocol, None, loop), transport, LIMIT)
        except BaseException:
            if transport:
                transport.close()
            if read_file:
                read_file.close()
            if write_file:
                write_file.close()
            for fd in (read_fd, write_fd):
                if fd >= 0:
                    os.close(fd)
            raise

    def __aiter__(self):
        return self

    async def __anext__(self):
        while not self._ready:
            data = await self.reader.read(65536)
            if not data:
                if self._frame or self._oversized is not None:
                    raise ConnectionError("Chrome disconnected in the middle of a CDP message")
                raise StopAsyncIteration
            parts = data.split(b"\0")
            for index, part in enumerate(parts):
                if self._oversized is None:
                    if len(self._frame) + len(part) > self.limit:
                        self._oversized = (bytes(self._frame[:PREFIX]) + part[:max(0, PREFIX - len(self._frame))])
                        self._frame.clear()
                    else:
                        self._frame.extend(part)
                if index < len(parts) - 1:
                    self._ready.append(OversizedFrame(self._oversized) if self._oversized is not None else bytes(self._frame))
                    self._frame.clear()
                    self._oversized = None
        return self._ready.popleft()

    async def send(self, message: str):
        try:
            async with asyncio.timeout(WRITE_TIMEOUT_S):
                async with self._send_lock:
                    self.writer.write(message.encode() + b"\0")
                    await self.writer.drain()
        except TimeoutError:
            # A partially written CDP frame cannot safely be reused.
            self.abort()
            raise

    def abort(self):
        if self._aborted:
            return
        self._aborted = True
        self.transport.close()
        if not self.writer.transport.is_closing():
            self.writer.transport.abort()  # discard buffered data; close() would wait for it to drain

    async def close(self):
        self.abort()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, TimeoutError):
            async with asyncio.timeout(CLOSE_TIMEOUT_S):
                await self.writer.wait_closed()
