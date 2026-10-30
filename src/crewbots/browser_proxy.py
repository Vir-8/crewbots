"""A task's HTTP proxy. Chrome must use it, including for loopback and WebSockets.

CDP request interception alone doesn't cover WebSockets. Tunnels are checked before
connecting; a policy change destroys the context (and its tunnels) before reuse.
"""

import asyncio
import base64
import contextlib
import fnmatch
import ipaddress
import secrets
from collections import deque
from datetime import UTC, datetime
from urllib.parse import urlsplit

Network = bool | tuple[str, ...]


def allowed(url: str, network: Network) -> bool:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https", "ws", "wss"} or not host or parsed.username or parsed.password:
            return False
        if host == "localhost":
            return True
        with contextlib.suppress(ValueError):
            if ipaddress.ip_address(host).is_loopback:
                return True
        return network is True or (bool(network) and any(fnmatch.fnmatchcase(host, p.lower()) for p in network))
    except (ValueError, TypeError):
        return False


class BrowserProxy:
    def __init__(self, network: Network):
        self.network = network
        self.username = "crewbots"
        self.password = secrets.token_urlsafe(32)
        self.realm = secrets.token_urlsafe(16)
        self.authorization = b"Basic " + base64.b64encode(f"{self.username}:{self.password}".encode())
        self.address = ""
        self.log = deque(maxlen=200)
        self.server = None
        self.connections: set[asyncio.Task] = set()
        self.streams: set[asyncio.StreamWriter] = set()
        self.closing = False
        self.blocked_ports: set[int] = set()

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._connected, "127.0.0.1", 0, limit=65536)
        port = self.server.sockets[0].getsockname()[1]
        self.blocked_ports.add(port)
        self.note("listening", address=f"127.0.0.1:{port}")
        self.address = f"http://127.0.0.1:{port}"
        return self.address

    def _connected(self, reader, writer):
        if self.closing:
            writer.transport.abort()
            return
        self.streams.add(writer)
        task = asyncio.create_task(self._accept(reader, writer))
        self.connections.add(task)
        task.add_done_callback(self._connection_done)

    def _connection_done(self, task):
        self.connections.discard(task)
        if not task.cancelled() and (error := task.exception()):
            self.note("connection_error", error=str(error))

    async def close(self):
        self.closing = True
        if self.server:
            self.server.close()  # Python 3.12 wait_closed also waits for accepted connections.
        for stream in list(self.streams):
            if not stream.transport.is_closing():
                stream.transport.abort()
        pending = list(self.connections)
        for task in pending:
            task.cancel()
        if pending:
            _, remaining = await asyncio.wait(pending, timeout=1)
            if remaining:
                self.note("close_timeout", connections=len(remaining))
        if self.server:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(1):
                    await self.server.wait_closed()

    async def _accept(self, reader, writer):
        upstream = None
        url = None
        self.note("accepted", peer=str(writer.get_extra_info("peername")))
        try:
            async with asyncio.timeout(15):
                header = await reader.readuntil(b"\r\n\r\n")
                line, *headers = header.decode("latin1").split("\r\n")
                method, target, version = line.split(" ")
                auth = [h.split(":", 1)[1].strip().encode("latin1") for h in headers
                        if h.lower().startswith("proxy-authorization:")]
                if len(auth) != 1 or not secrets.compare_digest(auth[0], self.authorization):
                    self.note("authentication_required")  # never record the credentials or headers
                    writer.write(("HTTP/1.1 407 Proxy Authentication Required\r\n"
                                  f'Proxy-Authenticate: Basic realm="{self.realm}"\r\n'
                                  "Content-Length: 0\r\nConnection: close\r\n\r\n").encode())
                    await writer.drain()
                    return
                tunnel = method == "CONNECT"
                url = "https://" + target if tunnel else target
                self.note("request", method=method, url=url)
                parsed = urlsplit(url)
                port = parsed.port or (443 if tunnel or parsed.scheme == "https" else 80)
                if not allowed(url, self.network) or port in self.blocked_ports:
                    self.note("blocked", url=url)
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                    return
                self.note("connecting", host=parsed.hostname, port=port)
                remote, upstream = await asyncio.open_connection(parsed.hostname, port)
                self.streams.add(upstream)
                if self.closing:
                    return
                self.note("connected", url=url, peer=str(upstream.get_extra_info("peername")))
                if tunnel:
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()
                else:
                    # Force close so a second absolute request can't smuggle another host through this connection.
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    headers = [h for h in headers if h and not h.lower().startswith(("proxy-", "host:", "connection:"))]
                    upgrade = any(h.lower().startswith("upgrade:") for h in headers)
                    headers += [f"Host: {parsed.netloc}", "Connection: Upgrade" if upgrade else "Connection: close"]
                    upstream.write((f"{method} {path} {version}\r\n" + "\r\n".join(headers) + "\r\n\r\n").encode("latin1"))
                    await upstream.drain()
            async def copy(source, destination):
                while chunk := await source.read(65536):
                    destination.write(chunk)
                    await destination.drain()
            pipes = [asyncio.create_task(copy(reader, upstream)), asyncio.create_task(copy(remote, writer))]
            try:
                await asyncio.wait(pipes, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pipe in pipes:
                    pipe.cancel()
                await asyncio.gather(*pipes, return_exceptions=True)
        except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
            self.note("error", url=url, error=f"{type(e).__name__}: {e}")  # no fallback to direct access
        finally:
            for stream in (upstream, writer):
                if stream:
                    self.streams.discard(stream)
                    if not stream.transport.is_closing():
                        stream.transport.abort()
                    with contextlib.suppress(OSError, TimeoutError):
                        async with asyncio.timeout(1):
                            await stream.wait_closed()
            self.note("closed", url=url)

    def note(self, kind, **data):
        self.log.append({"at": datetime.now(UTC).isoformat(), "kind": kind, **data})

    def refusal(self, since=None) -> str:
        """The latest request turned away or unreachable after the `since` entry, in words."""
        for entry in reversed(self.log):
            if entry is since:
                break
            if entry["kind"] == "blocked":
                return f"the proxy blocked {entry['url']}"
            if entry["kind"] == "error":
                return f"the proxy couldn't reach {entry['url'] or 'the site'}: {entry['error']}"
        return ""
