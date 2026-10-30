"""Browser tools use a fake CDP pipe; no test launches Chrome."""

import asyncio
import base64
import json
import os
from pathlib import Path

import httpx
import pytest
from fakes import FakeBackend, bot, make_crew

from crewbots.api import create_app
from crewbots.browser import Browser
from crewbots.browser_cdp import CDP, BrowserError
from crewbots.browser_pipe import Pipe
from crewbots.browser_proxy import BrowserProxy, allowed
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.mcp_server import build_server
from crewbots.runner import Runner
from crewbots.store import Store

PNG = base64.b64encode(b"fake png bytes").decode()


class FakeCDP:
    def __init__(self):
        self.calls = []
        self.socket = None
        self.number = 0
        self.url = "http://localhost:5173"
        self.session_targets = {}
        self.waiting_sessions = set()
        self.navigations = {}
        self.decided = set()
        self.intercept_url = None
        self.fail_requests = set()
        self.gone_sessions = set()  # dropped by Chrome without an event

    async def event(self, method, params, session=None):
        await self.socket.send(json.dumps({"method": method, "params": params, "sessionId": session}))

    async def handle(self, socket):
        self.socket = socket
        async for raw in socket:
            request = json.loads(raw)
            self.calls.append(request)
            method, params = request["method"], request["params"]
            self.number += 1
            result = {}
            if request.get("sessionId") in self.gone_sessions:
                await socket.send(json.dumps({"id": request["id"], "error": {"message": "Session with given id not found."}}))
                continue
            if method == "Runtime.evaluate" and params["expression"] == "new Promise(() => {})":
                continue  # a script that never finishes
            if params.get("requestId") in self.fail_requests:
                await socket.send(json.dumps({"id": request["id"], "error": {"message": "Session is already disposed"}}))
                continue
            if method == "Target.getTargets":
                result = {"targetInfos": []}
            elif method == "Target.createBrowserContext":
                result = {"browserContextId": f"context-{self.number}"}
            elif method == "Target.createTarget":
                target, session = f"target-{self.number}", f"auto-{self.number}"
                self.session_targets[session] = target
                self.waiting_sessions.add(session)
                await self.event("Target.attachedToTarget", {"sessionId": session, "waitingForDebugger": True,
                    "targetInfo": {"targetId": target, "url": "about:blank", "type": "page",
                                   "browserContextId": params["browserContextId"]}})
                result = {"targetId": target}
            elif method == "Target.attachToTarget":
                session = f"manual-{self.number}"
                self.session_targets[session] = params["targetId"]
                result = {"sessionId": session}
            elif method == "Runtime.runIfWaitingForDebugger":
                self.waiting_sessions.discard(request["sessionId"])
            elif method == "Page.navigate":
                self.url = params["url"]
                request_id = f"fetch-{request['id']}"
                self.navigations[request_id] = request
                await self.event("Fetch.requestPaused", {
                    "requestId": request_id, "request": {"url": self.intercept_url or params["url"]}}, request["sessionId"])
                continue  # Chrome cannot acknowledge navigation until every debugger and intercepted request resumes.
            elif method in {"Fetch.continueRequest", "Fetch.failRequest"}:
                self.decided.add(params["requestId"])
                navigation = self.navigations.get(params["requestId"])
                if navigation and not any(self.session_targets[s] == self.session_targets[navigation["sessionId"]]
                                          for s in self.waiting_sessions):
                    result_for_page = {"errorText": "net::ERR_BLOCKED_BY_CLIENT"} if method == "Fetch.failRequest" else {}
                    await socket.send(json.dumps({"id": navigation["id"], "result": result_for_page}))
                    await self.event("Page.loadEventFired", {}, navigation["sessionId"])
            elif method == "Page.captureScreenshot":
                result = {"data": PNG}
            elif method == "Page.getLayoutMetrics":
                result = {"cssContentSize": {"x": 0, "y": 0, "width": 1400, "height": 1600}}
            elif method == "Runtime.evaluate":
                expression = params["expression"]
                if expression == "throw Error('bad')":
                    result = {"exceptionDetails": {"exception": {"description": "bad"}}}
                else:
                    value = {"url": self.url, "title": "Preview"} if "document.title" in expression else {"x": 12, "y": 18}
                    result = {"result": {"value": value}}
            await socket.send(json.dumps({"id": request["id"], "result": result}))


async def pipe_pair():
    left_read, right_write = os.pipe()
    right_read, left_write = os.pipe()
    return await Pipe.open(left_read, left_write), await Pipe.open(right_read, right_write)


@pytest.fixture
async def browser(tmp_path, monkeypatch):
    fake = FakeCDP()
    peers, workers = [], []

    async def start(self):
        if self.cdp is None:
            transport, peer = await pipe_pair()
            fake.socket = peer
            peers.append(peer)
            workers.append(asyncio.create_task(fake.handle(peer)))
            self.cdp = CDP(transport)
            self.cdp.on_event = self._event
            self.cdp.on_event_error = self._event_error
    monkeypatch.setattr(Browser, "_start", start)
    instance = Browser(tmp_path / "profile")
    yield instance, fake
    await instance.close()
    for peer in peers:
        await peer.close()
    await asyncio.gather(*workers)


@pytest.mark.parametrize("url,network,expected", [
    ("http://localhost:5173", False, True), ("http://127.0.0.1:5173", False, True),
    ("http://[::1]:5173", False, True), ("http://localhost.evil.test", False, False),
    ("http://127.0.0.1.evil.test", False, False), ("https://example.com", False, False),
    ("https://example.com", True, True), ("https://cdn.example.com", ("*.example.com",), True),
    ("https://example.org", ("*.example.com",), False), ("ws://example.org", False, False),
    ("file:///etc/passwd", True, False), ("chrome://settings", True, False),
    ("http://localhost@evil.test", False, False), ("not a url", True, False),
])
def test_network_rule(url, network, expected):
    assert allowed(url, network) is expected


async def test_tools_keep_separate_contexts_and_return_saved_images(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173"})
    await service.action("b", tmp_path, True, "open", {"url": "https://example.com"})
    assert service.sessions["a"].context != service.sessions["b"].context
    assert service.sessions["a"].title == "Preview"
    for theme in ("light", "dark"):
        content = await service.action("a", tmp_path, False, "screenshot", {"theme": theme, "full_page": True})
        assert content[1] == {"type": "image", "data": PNG, "mimeType": "image/png"}
        assert Path(content[0]["text"]).parent == tmp_path
        assert Path(content[0]["text"]).read_bytes() == b"fake png bytes"
    sizes = [c["params"] for c in fake.calls if c["method"] == "Emulation.setDeviceMetricsOverride"]
    assert all(c["width"] == 1400 and c["height"] == 860 for c in sizes)
    themes = [c["params"]["features"][0]["value"] for c in fake.calls if c["method"] == "Emulation.setEmulatedMedia"]
    assert themes == ["light", "dark"]
    await service.action("a", tmp_path, False, "click", {"selector": "button"})
    await service.action("a", tmp_path, False, "type", {"selector": "input", "text": "hello", "key": "Enter"})
    assert [c["params"]["type"] for c in fake.calls if c["method"] == "Input.dispatchMouseEvent"] == [
        "mousePressed", "mouseReleased"]
    assert any(c["method"] == "Input.insertText" and c["params"] == {"text": "hello"} for c in fake.calls)
    with pytest.raises(BrowserError, match="bad"):
        await service.action("a", tmp_path, False, "evaluate", {"expression": "throw Error('bad')"})
    first = service.sessions["a"].context
    await service.close_task("a")
    assert "a" not in service.sessions and "b" in service.sessions
    assert any(c["method"] == "Target.disposeBrowserContext" and c["params"]["browserContextId"] == first for c in fake.calls)


async def test_requests_and_errors_from_cdp_are_handled(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    session = service.sessions["a"].session
    for url in ("https://example.com/redirect", "file:///etc/passwd", "http://localhost:5173/app.js"):
        await fake.event("Fetch.requestPaused", {"requestId": url, "request": {"url": url}}, session)
    await fake.event("Runtime.consoleAPICalled", {"type": "log", "args": [{"value": "hello"}]}, session)
    await fake.event("Runtime.exceptionThrown", {"exceptionDetails": {
        "text": "Uncaught", "exception": {"description": "oops"}}}, session)
    await fake.event("Target.attachedToTarget", {"targetInfo": {"targetId": "popup", "url": "https://example.com"}})
    await asyncio.sleep(0.05)
    failed = [c["params"]["requestId"] for c in fake.calls if c["method"] == "Fetch.failRequest"]
    assert failed == ["https://example.com/redirect", "file:///etc/passwd"]
    assert any(c["method"] == "Target.closeTarget" and c["params"]["targetId"] == "popup" for c in fake.calls)
    result = await service.action("a", tmp_path, False, "console", {"clear": True})
    assert "oops" in result[0]["text"] and "hello" in result[0]["text"]
    assert not service.sessions["a"].console


async def test_policy_change_and_idle_dispose_context(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, True, "open", {"url": "https://example.com"})
    old = service.sessions["a"].context
    service.idle_s = 0.02
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    assert service.sessions["a"].context != old
    await asyncio.sleep(0.06)
    assert not service.sessions and service.cdp is None
    assert len([c for c in fake.calls if c["method"] == "Target.disposeBrowserContext"]) == 2


async def test_refusal_does_not_start_chrome(browser, tmp_path):
    service, fake = browser
    for url in ("https://example.com", "file:///etc/passwd", "ws://localhost"):
        with pytest.raises(BrowserError, match="not allowed"):
            await service.action("a", tmp_path, False, "open", {"url": url})
    assert not fake.calls


async def test_proxy_blocks_external_http_connect_and_websocket():
    proxy = BrowserProxy(False)
    address = await proxy.start()
    port = int(address.rsplit(":", 1)[1])
    try:
        for request in (b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n",
                        b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",
                        b"GET ws://example.com/ HTTP/1.1\r\nUpgrade: websocket\r\n\r\n",
                        f"CONNECT localhost:{port} HTTP/1.1\r\n\r\n".encode()):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(request.replace(b"\r\n\r\n", b"\r\nProxy-Authorization: " + proxy.authorization + b"\r\n\r\n"))
            await writer.drain()
            assert b"403 Forbidden" in await reader.read()
            writer.close()
            await writer.wait_closed()
    finally:
        await proxy.close()


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
async def test_proxy_forwards_local_preview_and_closes_tunnels(host):
    async def echo(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    async with await asyncio.start_server(echo, host, 0) as server:
        origin = server.sockets[0].getsockname()[1]
        proxy = BrowserProxy(False)
        address = await proxy.start()
        try:
            async with httpx.AsyncClient(proxy=httpx.Proxy(address, auth=(proxy.username, proxy.password))) as client:
                response = await client.get(f"http://localhost:{origin}")
                assert response.text == "OK"
                connected = next(entry for entry in proxy.log if entry["kind"] == "connected")
                assert host in connected["peer"]
        finally:
            await proxy.close()
        assert not proxy.connections


@pytest.mark.parametrize("loop_name", ["asyncio", "uvloop"])
@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_proxy_upstream_connect_on_event_loops(loop_name, host):
    if loop_name == "uvloop":
        loop_factory = pytest.importorskip("uvloop").new_event_loop
    else:
        loop_factory = asyncio.new_event_loop

    async def exercise():
        async def origin(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with await asyncio.start_server(origin, host, 0) as server:
            port = server.sockets[0].getsockname()[1]
            proxy = BrowserProxy(False)
            address = await proxy.start()
            try:
                async with httpx.AsyncClient(proxy=httpx.Proxy(address, auth=(proxy.username, proxy.password))) as client:
                    response = await client.get(f"http://localhost:{port}")
                    assert response.text == "OK"
                reader, writer = await asyncio.open_connection("127.0.0.1", int(address.rsplit(":", 1)[1]))
                try:
                    writer.write(f"CONNECT localhost:{port} HTTP/1.1\r\n".encode()
                                 + b"Proxy-Authorization: " + proxy.authorization + b"\r\n\r\n")
                    await writer.drain()
                    assert b"200 Connection Established" in await reader.readuntil(b"\r\n\r\n")
                    writer.write(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    await writer.drain()
                    assert b"OK" in await reader.read()
                finally:
                    writer.close()
                    await writer.wait_closed()
                assert host in next(entry["peer"] for entry in proxy.log if entry["kind"] == "connected")
            finally:
                await proxy.close()

    with asyncio.Runner(loop_factory=loop_factory) as runner:
        runner.run(exercise())


async def test_tools_api_auth_logging_and_mcp_images(browser, tmp_path):
    service, fake = browser
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("engineer", workspace=tmp_path)), {"fake": FakeBackend()})
    runner.browser = service
    task = store.create_task("engineer", "browser")
    runner._tokens["run-token"] = task.id
    tools = CrewTools(runner, task.id, False)
    with pytest.raises(ToolRefused, match="not allowed"):
        await tools.call("browser_open", {"url": "https://example.com"})
    assert store.agent_log()[0]["kind"] == "refused"
    app = create_app(runner, "admin", 8765)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765/v1") as client:
        assert (await client.post("/bot/browser/open", json={"url": "http://localhost"})).status_code == 403
        response = await client.post("/bot/browser/open", json={"url": "http://localhost"},
                                     headers={"Authorization": "Bearer run-token"})
        assert response.status_code == 200 and "Preview" in response.json()["content"][0]["text"]
        assert (await client.post("/bot/browser/upload", json={},
                                   headers={"Authorization": "Bearer run-token"})).status_code == 422
    content = await tools.call("browser_screenshot", {})
    assert content[1]["type"] == "image"
    # The stdio MCP wrapper returns actual image blocks, not a JSON string containing an image.
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"content": content}))
    with httpx.Client(transport=transport, base_url="http://daemon/v1") as client:
        result = await build_server(client, False).call_tool("browser_screenshot", {})
        blocks = result.content
        assert any(getattr(block, "type", None) == "image" for block in blocks)
    await runner.cancel(task.id)
    assert not service.sessions
    await runner.shutdown()
    store.close()


async def test_chrome_start_failure_cleans_up(tmp_path, monkeypatch):
    async def failed(*args, **kwargs):
        kwargs["stderr"].write(b"Chrome diagnostic from fake launch")
        raise OSError("cannot start Chrome")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", failed)
    service = Browser(tmp_path / "browser")
    with pytest.raises(BrowserError, match="cannot start"):
        await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    assert service.profile is None and service.cdp is None and service.process is None
    assert service.chrome_stderr() == "Chrome diagnostic from fake launch"
    await service.close()


async def test_actions_require_an_open_page_and_policy_changes_require_reopening(browser, tmp_path):
    service, fake = browser
    with pytest.raises(BrowserError, match="Open an HTTP"):
        await service.action("a", tmp_path, False, "evaluate", {"expression": "location.href"})
    assert not fake.calls
    await service.action("a", tmp_path, True, "open", {"url": "https://example.com"})
    with pytest.raises(BrowserError, match="Open an HTTP"):
        await service.action("a", tmp_path, False, "evaluate", {"expression": "location.href"})
    assert "a" not in service.sessions
    with pytest.raises(BrowserError, match="Open an HTTP"):
        await service.action("a", tmp_path, False, "screenshot", {})


async def test_other_task_proxy_ports_are_blocked(browser, tmp_path):
    service, _ = browser
    for task in ("a", "b"):
        await service.action(task, tmp_path, True, "open", {"url": "http://localhost"})
    for task in ("a", "b"):
        proxy = service.sessions[task].proxy
        other = service.sessions["b" if task == "a" else "a"].proxy.server.sockets[0].getsockname()[1]
        port = proxy.server.sockets[0].getsockname()[1]
        for blocked in (other,):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"CONNECT 127.0.0.1:{blocked} HTTP/1.1\r\n".encode()
                         + b"Proxy-Authorization: " + proxy.authorization + b"\r\n\r\n")
            await writer.drain()
            assert b"403 Forbidden" in await reader.read()
            writer.close()
            await writer.wait_closed()


async def test_screenshot_cannot_overwrite_a_workspace_symlink(browser, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from crewbots import browser as module

    service, _ = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    target = tmp_path / "important.txt"
    target.write_text("keep")
    (tmp_path / "browser-light-fixed.png").symlink_to(target)
    monkeypatch.setattr(module, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    with pytest.raises(BrowserError, match="File exists"):
        await service.action("a", tmp_path, False, "screenshot", {})
    assert target.read_text() == "keep"


async def test_auto_attached_session_resumes_and_intercepted_navigation_completes(browser, tmp_path):
    service, fake = browser
    async with asyncio.timeout(1):
        result = await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173"})
    assert "Preview" in result[0]["text"]
    assert not fake.waiting_sessions
    assert set(fake.navigations) <= fake.decided
    assert not any(c["method"] == "Target.attachToTarget" for c in fake.calls)


async def test_an_unowned_paused_request_is_failed_instead_of_ignored(browser, tmp_path):
    service, fake = browser
    await service._start()
    await fake.event("Fetch.requestPaused", {"requestId": "orphan", "request": {"url": "http://localhost/"}}, "unowned")
    async with asyncio.timeout(1):
        while not any(c["method"] == "Fetch.failRequest" and c["params"]["requestId"] == "orphan" for c in fake.calls):
            await asyncio.sleep(0.01)
    failure = next(c for c in fake.calls if c["method"] == "Fetch.failRequest")
    assert failure["sessionId"] == "unowned"


async def test_timeout_names_the_step_session_and_paused_request(browser):
    service, fake = browser
    await service._start()
    service.cdp.timeout_s = 0.02
    # A deliberately stalled interceptor verifies the diagnostic, not a production handler.
    gate = asyncio.Event()
    async def stalled(message):
        await gate.wait()
    service.cdp.on_event = stalled
    with pytest.raises(BrowserError) as caught:
        await service.cdp.call("Page.navigate", {"url": "http://localhost:5173/stalled"}, "stalled-session")
    error = str(caught.value)
    assert "Page.navigate" in error and "stalled-session" in error
    assert "fetch-" in error and "http://localhost:5173/stalled" in error
    assert "paused requests" in error


async def test_diagnostics_dump_preserves_proxy_and_cdp_evidence(browser, tmp_path):
    import runpy

    service, _ = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173"})
    helpers = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "check-browser.py"))
    outputs = helpers["dump_diagnostics"](service, tmp_path)
    assert set(outputs) == {"chrome.stderr.log", "proxy.json", "cdp.json"}
    assert "listening" in (tmp_path / "proxy.json").read_text()
    cdp = json.loads((tmp_path / "cdp.json").read_text())
    assert any(event.get("method") == "Fetch.continueRequest" for event in cdp["cdp"]["trace"])
    assert cdp["sessions"]["a"]["session"].startswith("auto-")
    await service.close()
    assert service.diagnostics()["cdp"]["trace"]  # teardown keeps the last connection's trace


async def test_blocked_intercepted_navigation_finishes_with_an_error(browser, tmp_path):
    service, fake = browser
    fake.intercept_url = "https://external.example/redirect"
    async with asyncio.timeout(1):
        with pytest.raises(BrowserError, match="ERR_BLOCKED_BY_CLIENT"):
            await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173"})
    assert set(fake.navigations) <= fake.decided
    # Page.navigate may fail before Chrome acknowledges Fetch.failRequest on the other command.
    async with asyncio.timeout(1):
        while service.cdp.paused:
            await asyncio.sleep(0.01)


async def test_initial_blank_tab_rejects_unowned_requests(browser):
    service, fake = browser
    await service._start()
    service._startup_targets.add("initial-tab")
    await fake.event("Target.attachedToTarget", {"sessionId": "initial", "waitingForDebugger": True,
                     "targetInfo": {"targetId": "initial-tab", "url": "about:blank", "type": "page"}})
    async with asyncio.timeout(1):
        while not any(c["method"] == "Runtime.runIfWaitingForDebugger" for c in fake.calls):
            await asyncio.sleep(0.01)
    assert any(c["method"] == "Fetch.enable" and c["sessionId"] == "initial" for c in fake.calls)
    await fake.event("Fetch.requestPaused", {"requestId": "initial-request", "request": {"url": "http://localhost"}}, "initial")
    async with asyncio.timeout(1):
        while not any(c["method"] == "Fetch.failRequest" and c["params"]["requestId"] == "initial-request" for c in fake.calls):
            await asyncio.sleep(0.01)
    assert not any(c["method"] == "Target.closeTarget" for c in fake.calls)


async def test_navigation_error_includes_task_proxy_diagnostics(browser, tmp_path):
    service, fake = browser
    fake.intercept_url = "https://denied.example"
    with pytest.raises(BrowserError) as caught:
        await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    assert "ERR_BLOCKED_BY_CLIENT" in str(caught.value)
    assert tab.proxy.address in str(caught.value) and '"listening"' in str(caught.value)
    assert tab.proxy.password not in str(caught.value)


async def test_a_page_chrome_closes_or_crashes_is_dropped_and_said_why(browser, tmp_path):
    service, fake = browser
    for event, why in (("Target.detachedFromTarget", "Chrome closed it"), ("Inspector.targetCrashed", "it crashed")):
        await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173"})
        session = service.sessions["a"].session
        if event == "Target.detachedFromTarget":
            await fake.event(event, {"sessionId": session, "targetId": fake.session_targets[session]})
        else:
            await fake.event(event, {}, session)
        for _ in range(100):
            if "a" not in service.sessions:
                break
            await asyncio.sleep(0.01)
        with pytest.raises(BrowserError, match=f"the last one went because {why}"):
            await service.action("a", tmp_path, False, "evaluate", {"expression": "location.href"})
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173"})  # a new page, not the lost one
    assert service.sessions["a"].session != session


async def test_an_open_that_finds_its_page_gone_starts_a_new_one(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173"})
    lost = service.sessions["a"].session
    fake.gone_sessions.add(lost)  # Chrome dropped it and said nothing
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173/again"})
    assert service.sessions["a"].session not in {lost, None}


async def test_an_idle_page_and_a_long_script_say_what_happened(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173"})
    service.cdp.timeout_s = 0.05
    with pytest.raises(BrowserError, match="wait between calls rather than inside one"):
        await service.action("a", tmp_path, False, "evaluate", {"expression": "new Promise(() => {})"})
    service.idle_s = 0.02
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost:5173"})
    await asyncio.sleep(0.08)
    with pytest.raises(BrowserError, match="the last one went because no browser call came for"):
        await service.action("a", tmp_path, False, "evaluate", {"expression": "location.href"})

