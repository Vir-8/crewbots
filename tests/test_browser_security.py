"""Browser policy boundaries, with authenticated proxies and fake pipe CDP."""

import asyncio
import json

import httpx
import pytest
from fakes import wait_until
from test_browser import browser as browser

from crewbots.browser_cdp import BrowserError
from crewbots.browser_proxy import BrowserProxy


async def test_proxies_require_their_own_secret_for_http_connect_and_websockets():
    first, second = BrowserProxy(True), BrowserProxy(True)
    await first.start()
    address = await second.start()
    port = int(address.rsplit(":", 1)[1])
    assert first.password != second.password and first.realm != second.realm
    try:
        for line in ("GET http://example.test/ HTTP/1.1", "CONNECT example.test:443 HTTP/1.1",
                     "GET ws://example.test/ HTTP/1.1"):
            for auth in (b"", b"Proxy-Authorization: " + first.authorization + b"\r\n"):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(line.encode() + b"\r\n" + auth + b"\r\n")
                await writer.drain()
                response = await reader.read()
                assert b"407 Proxy Authentication Required" in response
                assert b"Proxy-Authenticate: Basic" in response
                writer.close()
                await writer.wait_closed()
        assert not any(entry["kind"] == "connecting" for entry in second.log)
        assert second.password not in json.dumps(list(second.log))
        assert first.password not in json.dumps(list(second.log))
    finally:
        await first.close()
        await second.close()


async def test_proxy_authentication_is_stripped_before_origin_request():
    received = []

    async def origin(reader, writer):
        received.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(origin, "127.0.0.1", 0) as server:
        origin_port = server.sockets[0].getsockname()[1]
        proxy = BrowserProxy(False)
        address = await proxy.start()
        try:
            async with httpx.AsyncClient(proxy=httpx.Proxy(address, auth=(proxy.username, proxy.password))) as client:
                assert (await client.get(f"http://127.0.0.1:{origin_port}/")).text == "OK"
            assert len(received) == 1
            assert b"proxy-authorization" not in received[0].lower()
            assert proxy.authorization not in received[0]
        finally:
            await proxy.close()


async def test_cdp_answers_only_its_own_proxy_challenge_and_never_logs_secrets(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    await service.action("b", tmp_path, True, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    other = service.sessions["b"]
    assert any(c["method"] == "Fetch.enable" and c["params"]["handleAuthRequests"] for c in fake.calls)
    base = {"source": "Proxy", "scheme": "basic", "realm": tab.proxy.realm, "origin": tab.proxy.address}
    variants = [base, {**base, "source": "Server"}, {**base, "origin": other.proxy.address},
                {**base, "realm": "wrong"}, {**base, "scheme": "digest"}]
    for number, challenge in enumerate(variants):
        await fake.event("Fetch.authRequired", {"requestId": str(number), "authChallenge": challenge}, tab.session)
    await wait_until(lambda: len([c for c in fake.calls if c["method"] == "Fetch.continueWithAuth"]) == 5)
    answered = [c["params"]["authChallengeResponse"] for c in fake.calls if c["method"] == "Fetch.continueWithAuth"]
    assert answered[0] == {"response": "ProvideCredentials", "username": tab.proxy.username, "password": tab.proxy.password}
    assert all(answer == {"response": "CancelAuth"} for answer in answered[1:])
    await fake.event("Fetch.authRequired", {"requestId": "0", "authChallenge": base}, tab.session)
    await wait_until(lambda: len([c for c in fake.calls if c["method"] == "Fetch.continueWithAuth"]) == 6)
    assert fake.calls[-1]["params"]["authChallengeResponse"] == {"response": "CancelAuth"}
    assert tab.proxy.password not in json.dumps(service.diagnostics())
    assert other.proxy.password not in json.dumps(service.diagnostics())


async def test_rejected_open_disposes_a_revoked_policy_before_refusing(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, True, "open", {"url": "https://example.com"})
    old = service.sessions["a"]
    with pytest.raises(BrowserError, match="not allowed"):
        await service.action("a", tmp_path, False, "open", {"url": "https://example.com"})
    assert "a" not in service.sessions
    assert not old.proxy.server.is_serving()
    assert old.idle.cancelled()
    assert service.cdp is None
    assert any(c["method"] == "Target.disposeBrowserContext" and c["params"]["browserContextId"] == old.context
               for c in fake.calls)


async def test_refused_actions_do_not_renew_a_context_idle_timer(browser, tmp_path):
    service, _ = browser
    service.idle_s = 0.1
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    idle = service.sessions["a"].idle
    for _ in range(3):
        with pytest.raises(BrowserError, match="not allowed"):
            await service.action("a", tmp_path, False, "open", {"url": "https://example.com"})
        assert service.sessions["a"].idle is idle
    await wait_until(lambda: not service.sessions)
    await wait_until(lambda: service.cdp is None)


async def test_each_task_expires_independently_and_last_context_stops_chrome(browser, tmp_path):
    service, _ = browser
    service.idle_s = 0.05
    await service.action("quiet", tmp_path, False, "open", {"url": "http://localhost"})
    quiet = service.sessions["quiet"]
    service.idle_s = 10
    await service.action("active", tmp_path, False, "open", {"url": "http://localhost"})
    await service.action("active", tmp_path, False, "console", {})
    await wait_until(lambda: "quiet" not in service.sessions)
    assert "active" in service.sessions and service.cdp is not None
    assert not quiet.proxy.server.is_serving()
    await service.close_task("active")
    assert service.cdp is None


async def test_late_request_from_disposed_session_does_not_disconnect_other_tasks(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    await service.action("b", tmp_path, False, "open", {"url": "http://localhost"})
    old_session = service.sessions["a"].session
    await service.close_task("a")
    fake.fail_requests.add("late")
    await fake.event("Fetch.requestPaused", {"requestId": "late", "request": {"url": "http://localhost"}}, old_session)
    await wait_until(lambda: service.cdp.event_error is not None)
    assert not service.cdp.reader.done()
    assert "b" in service.sessions
    assert await service.action("b", tmp_path, False, "evaluate", {"expression": "1 + 1"})
    assert any(entry["kind"] == "event_error" and entry["session"] == old_session for entry in service.cdp.trace)


@pytest.mark.parametrize("another_task", [False, True])
async def test_handler_failure_closes_only_its_context(browser, tmp_path, another_task):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    if another_task:
        await service.action("b", tmp_path, False, "open", {"url": "http://localhost"})
    session = service.sessions["a"].session
    fake.fail_requests.add("bad")
    await fake.event("Fetch.requestPaused", {"requestId": "bad", "request": {"url": "http://localhost"}}, session)
    await wait_until(lambda: "a" not in service.sessions)
    if another_task:
        assert "b" in service.sessions and not service.cdp.reader.done()
    else:
        await wait_until(lambda: service.cdp is None)


@pytest.mark.parametrize("url", ["", "about:blank"])
async def test_blank_popups_are_closed_without_being_left_paused(browser, tmp_path, url):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    context = service.sessions["a"].context
    await fake.event("Target.attachedToTarget", {"sessionId": "popup-session", "waitingForDebugger": True,
        "targetInfo": {"targetId": "popup", "url": url, "browserContextId": context}})
    await wait_until(lambda: any(c["method"] == "Target.closeTarget" and c["params"]["targetId"] == "popup" for c in fake.calls))
    assert "a" in service.sessions
