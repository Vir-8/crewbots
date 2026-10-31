"""Browser policy boundaries, with authenticated proxies and fake pipe CDP."""

import asyncio
import json
import sys
from unittest.mock import AsyncMock

import httpx
import pytest
from fakes import wait_until
from test_browser import browser as browser

from crewbots.browser import Browser
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


async def test_event_failure_interrupts_navigation_without_waiting_for_action_lock(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    await service.action("b", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    original = service.cdp.on_event

    async def fail_request(message):
        if message.get("method") == "Fetch.requestPaused" and message.get("sessionId") == tab.session:
            raise BrowserError("interception failed")
        await original(message)

    service.cdp.on_event = fail_request
    async with asyncio.timeout(1):
        with pytest.raises(BrowserError, match="interception failed"):
            await service.action("a", tmp_path, False, "open", {"url": "http://localhost/next"})
        await wait_until(lambda: "a" not in service.sessions)
    assert not tab.proxy.server.is_serving()
    assert "b" in service.sessions and await service.action("b", tmp_path, False, "console", {})


async def test_event_failure_interrupts_wait_for_load(browser, tmp_path, monkeypatch):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    original = service.cdp.call
    navigating = asyncio.Event()

    async def no_load(method, params=None, session=None):
        if method == "Page.navigate":
            navigating.set()
            return {}
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", no_load)
    fake.fail_requests.add("bad-load")
    action = asyncio.create_task(service.action("a", tmp_path, False, "open", {"url": "http://localhost/next"}))
    async with asyncio.timeout(1):
        await navigating.wait()
        await fake.event("Fetch.requestPaused", {"requestId": "bad-load", "request": {"url": "http://localhost"}}, tab.session)
        with pytest.raises(BrowserError, match="Session is already disposed"):
            await action
        await wait_until(lambda: "a" not in service.sessions)
    assert not tab.proxy.server.is_serving()


@pytest.mark.parametrize("url", ["about:blank", "", "https://unexpected.example/"])
async def test_startup_target_with_context_is_intercepted(tmp_path, monkeypatch, url):
    service = Browser(tmp_path)
    initial = {"targetId": "initial-tab", "type": "page", "url": url, "browserContextId": "default-context"}
    other = {"targetId": "worker", "type": "service_worker", "url": "about:blank"}

    class InitialCDP:
        def __init__(self, transport):
            self.call = AsyncMock(side_effect=self.respond)

        async def respond(self, method, params=None, session=None):
            return {"targetInfos": [initial, other]} if method == "Target.getTargets" else {}

    monkeypatch.setattr("crewbots.browser.CDP", InitialCDP)
    monkeypatch.setattr("crewbots.browser.launch", AsyncMock(return_value=(object(), object())))
    monkeypatch.setenv("CREWBOTS_CHROME", sys.executable)
    monkeypatch.setattr(service._profiles, "acquire", AsyncMock())
    monkeypatch.setattr(service, "_stop", AsyncMock())
    await service._start()
    assert service._startup_targets == {"initial-tab"}
    # Even a simultaneous context creation must not claim the pre-existing target.
    creating = asyncio.get_running_loop().create_future()
    service._creating["default-context"] = creating
    await service._event({"method": "Target.attachedToTarget", "params": {
        "sessionId": "initial", "targetInfo": initial}})
    assert not creating.done()
    assert "initial-tab" not in service._startup_targets
    assert [call.args[0] for call in service.cdp.call.await_args_list][-2:] == [
        "Fetch.enable", "Runtime.runIfWaitingForDebugger"]
    service._log.close()


async def test_unrecognized_root_page_close_failure_stops_browser(browser, tmp_path, monkeypatch):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    original = service.cdp.call

    async def fail_close(method, params=None, session=None):
        if method == "Target.closeTarget":
            return {"success": False}
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", fail_close)
    info = {"targetId": "unknown-page", "type": "page", "url": ""}
    await fake.event("Target.attachedToTarget", {"sessionId": "unknown", "waitingForDebugger": True, "targetInfo": info})
    await wait_until(lambda: service.cdp is None)
    assert not service.sessions and not tab.proxy.server.is_serving()


@pytest.mark.parametrize("context", ["task", "default", "startup"])
async def test_missing_root_target_does_not_stop_browser(browser, tmp_path, monkeypatch, context):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    await service.action("b", tmp_path, False, "open", {"url": "http://localhost"})
    original = service.cdp.call
    called = asyncio.Event()

    async def missing_target(method, params=None, session=None):
        if method == "Fetch.enable" and context == "startup" and session == "popup-session":
            raise BrowserError("cannot intercept")
        if method == "Target.closeTarget":
            called.set()
            raise BrowserError("No target with given id found")
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", missing_target)
    if context == "startup":
        service._startup_targets.add("popup")
    info = {"targetId": "popup", "type": "page", "url": "about:blank"}
    if context == "task":
        info["browserContextId"] = service.sessions["a"].context
    await fake.event("Target.attachedToTarget", {"sessionId": "popup-session", "waitingForDebugger": True,
                                                  "targetInfo": info})
    await wait_until(called.is_set)
    await wait_until(lambda: not service.cdp.events)
    assert "popup" not in service._startup_targets
    assert set(service.sessions) == {"a", "b"}
    assert await service.action("b", tmp_path, False, "console", {})


async def test_uncloseable_task_popup_disposes_only_owning_context(browser, tmp_path, monkeypatch):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    await service.action("b", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    original = service.cdp.call

    async def fail_close(method, params=None, session=None):
        if method == "Target.closeTarget":
            raise BrowserError("cannot close")
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", fail_close)
    await fake.event("Target.attachedToTarget", {"sessionId": "popup-session", "waitingForDebugger": True,
        "targetInfo": {"targetId": "popup", "type": "page", "url": "about:blank", "browserContextId": tab.context}})
    await wait_until(lambda: "a" not in service.sessions)
    assert not tab.proxy.server.is_serving()
    assert "b" in service.sessions and service.cdp is not None and not service.cdp.reader.done()
    assert await service.action("b", tmp_path, False, "console", {})


async def test_failed_popup_close_does_not_dispose_owning_task(browser, tmp_path, monkeypatch, caplog):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    original = service.cdp.call

    async def fail_close(method, params=None, session=None):
        if method == "Target.closeTarget":
            return {"success": False}
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", fail_close)
    tab = service.sessions["a"]
    await fake.event("Target.attachedToTarget", {"sessionId": "child", "waitingForDebugger": True,
        "targetInfo": {"targetId": "iframe", "type": "iframe", "url": "http://localhost/frame",
                       "browserContextId": tab.context}}, tab.session)
    await wait_until(lambda: any(entry["kind"] == "target_close_error" for entry in service.cdp.trace))
    assert "a" in service.sessions and not service.cdp.reader.done()
    assert "Could not close browser target iframe" in caplog.text


@pytest.mark.parametrize("ordering", ["before", "during", "after", "failure_during"])
async def test_popup_close_failure_racing_disposal_keeps_other_task(browser, tmp_path, monkeypatch, ordering):
    service, fake = browser
    if ordering in {"during", "failure_during"}:
        monkeypatch.setattr("crewbots.browser.CONTEXT_GRACE_S", 0.01)
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    await service.action("b", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    original = service.cdp.call
    close_started, release_close = asyncio.Event(), asyncio.Event()
    disposal_started, release_disposal = asyncio.Event(), asyncio.Event()

    async def raced_call(method, params=None, session=None):
        if method == "Target.closeTarget" and params["targetId"] == "racing-popup":
            close_started.set()
            await release_close.wait()
            raise BrowserError("Specified target doesn't support closing")
        if method == "Target.disposeBrowserContext" and params["browserContextId"] == tab.context:
            disposal_started.set()
            await release_disposal.wait()
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", raced_call)

    async def popup():
        await fake.event("Target.attachedToTarget", {"sessionId": "popup-session", "waitingForDebugger": True,
            "targetInfo": {"targetId": "racing-popup", "type": "page", "browserContextId": tab.context}})
        await wait_until(close_started.is_set)

    if ordering == "before":
        await popup()
    disposing = asyncio.create_task(service.close_task("a"))
    await wait_until(disposal_started.is_set)
    if ordering in {"during", "failure_during"}:
        await asyncio.sleep(0.03)  # Disposal can take longer than the post-disposal grace period.
        await popup()
    if ordering == "failure_during":
        release_close.set()
        await wait_until(lambda: service.cdp.event_error is not None)
    release_disposal.set()
    await disposing
    if ordering == "after":
        await popup()
    release_close.set()
    await wait_until(lambda: service.cdp is None or not service.cdp.events)
    assert "a" not in service.sessions and "b" in service.sessions
    assert service.cdp is not None and not service.cdp.reader.done()
    assert not tab.proxy.server.is_serving()
    assert await service.action("b", tmp_path, False, "console", {})


async def test_popup_timeout_after_grace_keeps_captured_owner(browser, tmp_path, monkeypatch):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    await service.action("b", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    cdp = service.cdp
    cdp.timeout_s = 0.2
    original_send = cdp.transport.send
    close_sent = asyncio.Event()
    monkeypatch.setattr("crewbots.browser.CONTEXT_GRACE_S", 0.01)

    async def drop_close(raw):
        message = json.loads(raw)
        if message["method"] == "Target.closeTarget" and message["params"]["targetId"] == "popup":
            close_sent.set()
            return
        await original_send(raw)

    monkeypatch.setattr(cdp.transport, "send", drop_close)
    await fake.event("Target.attachedToTarget", {"sessionId": "popup-session", "waitingForDebugger": True,
        "targetInfo": {"targetId": "popup", "type": "page", "browserContextId": tab.context}})
    await wait_until(close_sent.is_set)
    await service.close_task("a")
    await wait_until(lambda: tab.context not in service._disposing)
    await wait_until(lambda: cdp.event_error is not None and not cdp.events)
    assert "Chrome timed out" in cdp.event_error
    assert service.cdp is cdp and not cdp.reader.done() and "b" in service.sessions
    assert await service.action("b", tmp_path, False, "console", {})


async def test_popup_after_grace_is_unknown_and_stops_browser(browser, tmp_path, monkeypatch):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    await service.action("b", tmp_path, False, "open", {"url": "http://localhost"})
    context = service.sessions["a"].context
    monkeypatch.setattr("crewbots.browser.CONTEXT_GRACE_S", 0.01)
    await service.close_task("a")
    await wait_until(lambda: context not in service._disposing)
    original = service.cdp.call

    async def fail_close(method, params=None, session=None):
        if method == "Target.closeTarget":
            return {"success": False}
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", fail_close)
    await fake.event("Target.attachedToTarget", {"sessionId": "popup-session", "waitingForDebugger": True,
        "targetInfo": {"targetId": "popup", "type": "page", "browserContextId": context}})
    await wait_until(lambda: service.cdp is None)
    assert not service.sessions


async def test_startup_interception_failure_closes_only_startup_target(browser, tmp_path, monkeypatch):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    original = service.cdp.call

    async def fail_initial(method, params=None, session=None):
        if method == "Fetch.enable" and session == "initial":
            raise BrowserError("cannot intercept")
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", fail_initial)
    service._startup_targets.add("initial-tab")
    await fake.event("Target.attachedToTarget", {"sessionId": "initial", "waitingForDebugger": True,
        "targetInfo": {"targetId": "initial-tab", "url": "about:blank", "type": "page"}})
    await wait_until(lambda: any(c["method"] == "Target.closeTarget" and c["params"]["targetId"] == "initial-tab"
                                 for c in fake.calls))
    assert not any(c["method"] == "Runtime.runIfWaitingForDebugger" and c.get("sessionId") == "initial" for c in fake.calls)
    assert await service.action("a", tmp_path, False, "console", {})


@pytest.mark.parametrize("close_succeeds", [True, False])
async def test_startup_resume_failure_closes_target_or_stops_browser(browser, tmp_path, monkeypatch, close_succeeds):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    original = service.cdp.call

    async def fail_resume(method, params=None, session=None):
        if method == "Runtime.runIfWaitingForDebugger" and session == "initial":
            raise BrowserError("cannot resume")
        if method == "Target.closeTarget" and not close_succeeds:
            return {"success": False}
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", fail_resume)
    service._startup_targets.add("initial-tab")
    await fake.event("Target.attachedToTarget", {"sessionId": "initial", "waitingForDebugger": True,
        "targetInfo": {"targetId": "initial-tab", "url": "about:blank", "type": "page"}})
    if close_succeeds:
        await wait_until(lambda: any(c["method"] == "Target.closeTarget" and c["params"]["targetId"] == "initial-tab"
                                     for c in fake.calls))
        await wait_until(lambda: "initial-tab" not in service._startup_targets)
        assert await service.action("a", tmp_path, False, "console", {})
    else:
        await wait_until(lambda: service.cdp is None)
        assert not tab.proxy.server.is_serving() and not service.sessions
    assert any(c["method"] == "Fetch.enable" and c.get("sessionId") == "initial" for c in fake.calls)


async def test_uncloseable_unprotected_startup_tab_shuts_down_service(browser, tmp_path, monkeypatch):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]
    original = service.cdp.call

    async def fail_initial(method, params=None, session=None):
        if method == "Fetch.enable" and session == "initial":
            raise BrowserError("cannot intercept")
        if method == "Target.closeTarget":
            return {"success": False}
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", fail_initial)
    service._startup_targets.add("initial-tab")
    await fake.event("Target.attachedToTarget", {"sessionId": "initial", "waitingForDebugger": True,
        "targetInfo": {"targetId": "initial-tab", "url": "about:blank", "type": "page"}})
    await wait_until(lambda: service.cdp is None)
    assert not tab.proxy.server.is_serving() and not service.sessions


@pytest.mark.parametrize("url", ["", "about:blank"])
async def test_blank_popups_are_closed_without_being_left_paused(browser, tmp_path, url):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    context = service.sessions["a"].context
    await fake.event("Target.attachedToTarget", {"sessionId": "popup-session", "waitingForDebugger": True,
        "targetInfo": {"targetId": "popup", "url": url, "browserContextId": context}})
    await wait_until(lambda: any(c["method"] == "Target.closeTarget" and c["params"]["targetId"] == "popup" for c in fake.calls))
    assert "a" in service.sessions
