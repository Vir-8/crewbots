"""Regression coverage for round 3; all subprocesses here are Python stand-ins."""

import asyncio
import json
import os
import sys

import pytest
from fakes import wait_until
from test_browser import browser as browser
from test_browser import pipe_pair

from crewbots.browser import Browser, BrowserSession
from crewbots.browser_cdp import CDP, BrowserError
from crewbots.browser_pipe import Pipe
from crewbots.browser_process import launch
from crewbots.browser_proxy import BrowserProxy


@pytest.mark.parametrize("size", [100, 1_000_000])
async def test_close_interrupts_blocked_write_and_terminates_child(tmp_path, size):
    script = tmp_path / "no_reader.py"
    script.write_text("import time\ntime.sleep(30)\n")
    with (tmp_path / "child.log").open("wb") as log:
        process, transport = await launch([sys.executable, str(script)], log)
        service = Browser(tmp_path / "browser")
        service.process, service.cdp = process, CDP(transport)
        service.sessions["a"] = BrowserSession("a", "context", "target", "session", False, BrowserProxy(False),
                                                url="http://localhost")
        action = asyncio.create_task(service.action("a", tmp_path, False, "evaluate", {"expression": "x" * size}))
        try:
            await wait_until(lambda: service._lock.locked())
            await asyncio.sleep(0.05)
            assert not action.done()
            async with asyncio.timeout(2.5):
                await service.close()
            assert process.returncode is not None
            assert not service.sessions
        finally:
            action.cancel()
            transport.abort()
            if process.returncode is None:
                process.kill()
                await process.wait()
            await asyncio.gather(action, return_exceptions=True)


@pytest.mark.parametrize("session_first", [False, True])
async def test_oversized_response_fails_its_call_then_reader_handles_next(monkeypatch, session_first):
    from crewbots import browser_pipe

    monkeypatch.setattr(browser_pipe, "LIMIT", 1024)
    transport, peer = await pipe_pair()
    cdp = CDP(transport)

    async def fake():
        first = json.loads(await anext(peer))
        response = {"id": first["id"], "result": {"data": "x" * 5000}}
        if session_first:
            response = {"sessionId": "child", **response}
        await peer.send(json.dumps(response))
        second = json.loads(await anext(peer))
        await peer.send(json.dumps({"id": second["id"], "result": {"ok": True}}))

    worker = asyncio.create_task(fake())
    try:
        with pytest.raises(BrowserError, match="exceeds|oversized"):
            async with asyncio.timeout(1):
                await cdp.call("Large")
        assert await cdp.call("Small") == {"ok": True}
        assert not cdp.reader.done()
    finally:
        await cdp.close()
        await peer.close()
        await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize("fail_mode", ["rb", "wb"])
async def test_fdopen_failure_closes_both_descriptors(monkeypatch, fail_mode):
    read_fd, read_peer = os.pipe()
    write_peer, write_fd = os.pipe()
    original = os.fdopen

    def fdopen(fd, mode, **kwargs):
        if mode == fail_mode:
            raise OSError("fake fdopen failure")
        return original(fd, mode, **kwargs)

    monkeypatch.setattr(os, "fdopen", fdopen)
    try:
        with pytest.raises(OSError, match="fake fdopen failure"):
            await Pipe.open(read_fd, write_fd)
        for fd in (read_fd, write_fd):
            with pytest.raises(OSError):
                os.fstat(fd)
    finally:
        for fd in (read_fd, write_fd, read_peer, write_peer):
            try:
                os.close(fd)
            except OSError:
                pass


@pytest.mark.parametrize("failure", ["error", "false"])
async def test_failed_popup_close_does_not_dispose_owning_task(browser, tmp_path, monkeypatch, caplog, failure):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    original = service.cdp.call

    async def fail_close(method, params=None, session=None):
        if method == "Target.closeTarget":
            if failure == "false":
                return {"success": False}
            raise BrowserError("This target cannot be closed")
        return await original(method, params, session)

    monkeypatch.setattr(service.cdp, "call", fail_close)
    tab = service.sessions["a"]
    await fake.event("Target.attachedToTarget", {"sessionId": "child", "waitingForDebugger": True,
        "targetInfo": {"targetId": "iframe", "type": "iframe", "url": "http://localhost/frame",
                       "browserContextId": tab.context}}, tab.session)
    await asyncio.sleep(0.1)
    assert "a" in service.sessions and not service.cdp.reader.done()
    assert any(entry["kind"] == "target_close_error" for entry in service.cdp.trace)
    assert "Could not close browser target iframe" in caplog.text


async def test_idle_expiration_failure_is_logged_and_deregisters_session(browser, tmp_path, monkeypatch, caplog):
    service, _ = browser
    service.idle_s = 0.02
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    tab = service.sessions["a"]

    async def fail_close(task_id):
        raise OSError("fake idle cleanup error")

    monkeypatch.setattr(service, "_close_task", fail_close)
    await wait_until(lambda: "a" not in service.sessions)
    assert "fake idle cleanup error" in caplog.text
    assert not tab.proxy.server.is_serving()


async def test_oversized_event_does_not_fail_nested_id_and_next_frame_survives(monkeypatch):
    from crewbots import browser_pipe

    monkeypatch.setattr(browser_pipe, "LIMIT", 1024)
    transport, peer = await pipe_pair()
    cdp = CDP(transport)

    async def fake():
        command = json.loads(await anext(peer))
        event = {"method": "Runtime.consoleAPICalled", "params": {"id": command["id"], "data": "x" * 5000}}
        response = {"sessionId": "s", "id": command["id"], "result": {"ok": True}}
        # Two NUL-delimited frames delivered together must not lose the second one.
        await peer.send(json.dumps(event) + "\0" + json.dumps(response))

    worker = asyncio.create_task(fake())
    try:
        assert await cdp.call("AfterEvent") == {"ok": True}
        assert not cdp.reader.done()
        assert any(item["kind"] == "oversized_frame" and item["id"] is None for item in cdp.trace)
    finally:
        await cdp.close()
        await peer.close()
        await asyncio.gather(worker, return_exceptions=True)


async def test_smoke_profile_root_never_trusts_bot_writable_owner_records(tmp_path, monkeypatch):
    from crewbots import browser_process

    root = tmp_path / "smoke"
    profile = root / "chrome-forged"
    profile.mkdir(parents=True)
    (profile / browser_process.OWNER_FILE).write_text(json.dumps({"pid": 101, "start_time": "forged"}))
    monkeypatch.setattr(browser_process, "process_info", lambda pid: pytest.fail("custom roots must not be swept"))
    monkeypatch.setattr(browser_process.os, "kill", lambda *args: pytest.fail("must not signal for a custom root"))
    service = Browser(root)
    await service._profiles.acquire()
    service._profiles.close()
    assert profile.exists()


async def test_write_times_out_even_without_a_shutdown(tmp_path):
    script = tmp_path / "no_reader.py"
    script.write_text("import time\ntime.sleep(30)\n")
    with (tmp_path / "child.log").open("wb") as log:
        process, transport = await launch([sys.executable, str(script)], log)
        cdp = CDP(transport)
        try:
            async with asyncio.timeout(2.5):
                with pytest.raises(BrowserError, match="timed out"):
                    await cdp.call("Runtime.evaluate", {"expression": "x" * 1_000_000})
            assert transport._aborted
        finally:
            await cdp.close()
            if process.returncode is None:
                process.kill()
            await process.wait()


async def test_failed_launcher_reaping_is_bounded_and_preserves_profile(tmp_path, monkeypatch, caplog):
    from crewbots import browser as browser_module
    from crewbots import browser_process

    class StuckProcess:
        pid = 999
        returncode = None
        killed = False

        def kill(self):
            self.killed = True

        async def wait(self):
            await asyncio.Event().wait()

    process = StuckProcess()

    async def spawn(*args, **kwargs):
        return process

    async def fail_open(*args, **kwargs):
        raise OSError("cannot open pipe")

    with monkeypatch.context() as patch:
        patch.setattr(browser_process.asyncio, "create_subprocess_exec", spawn)
        patch.setattr(browser_process.Pipe, "open", fail_open)
        with (tmp_path / "log").open("wb") as log:
            async with asyncio.timeout(2.5):
                with pytest.raises(browser_process.LaunchStillRunning, match="Could not stop"):
                    await browser_process.launch([sys.executable], log)
        assert process.killed

    async def stuck_launch(argv, log, profile):
        log.write(b"diagnostic")
        raise browser_process.LaunchStillRunning("launcher still running")

    monkeypatch.setattr(browser_module, "launch", stuck_launch)
    service = Browser(tmp_path / "profiles")
    with pytest.raises(browser_process.LaunchStillRunning, match="still running"):
        await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    left = list((tmp_path / "profiles").glob("chrome-*"))
    assert len(left) == 1 and (left[0] / "chrome.log").read_bytes() == b"diagnostic"
    assert "Leaving browser profile" in caplog.text and service.profile is None
    await service.close()


async def test_proxy_close_aborts_backpressured_upstream():
    release = asyncio.Event()
    connected = asyncio.Event()

    async def no_reader(reader, writer):
        connected.set()
        try:
            await release.wait()
        finally:
            writer.transport.abort()

    async with await asyncio.start_server(no_reader, "127.0.0.1", 0) as origin:
        origin_port = origin.sockets[0].getsockname()[1]
        proxy = BrowserProxy(False)
        address = await proxy.start()
        port = int(address.rsplit(":", 1)[1])
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(f"POST http://127.0.0.1:{origin_port}/ HTTP/1.1\r\nContent-Length: 1000000\r\n".encode()
                         + b"Proxy-Authorization: " + proxy.authorization + b"\r\n\r\n" + b"x" * 1_000_000)
            async with asyncio.timeout(1):
                await connected.wait()
            async with asyncio.timeout(2.5):
                await proxy.close()
            assert not proxy.connections
        finally:
            writer.transport.abort()
            release.set()
