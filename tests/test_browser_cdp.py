"""The CDP pipe's framing and its calls, against a Python peer or a child that never reads."""

import asyncio
import json
import os
import sys

import pytest
from test_browser import pipe_pair

from crewbots.browser_cdp import CDP, BrowserError, response_id
from crewbots.browser_pipe import Pipe
from crewbots.browser_process import launch


async def test_malformed_frames_do_not_disconnect_other_calls(caplog):
    transport, peer = await pipe_pair()
    cdp = CDP(transport)

    async def fake():
        command = json.loads(await anext(peer))
        await peer.send('{bad json}\0[]\0' + json.dumps({"id": command["id"], "result": {"ok": True}}))

    worker = asyncio.create_task(fake())
    try:
        assert await cdp.call("AfterBadFrames") == {"ok": True}
        assert not cdp.reader.done()
        assert sum(item["kind"] == "malformed_frame" for item in cdp.trace) == 2
        assert "Ignoring malformed Chrome CDP frame" in caplog.text
    finally:
        await cdp.close()
        await peer.close()
        await worker


@pytest.mark.parametrize("prefix,expected", [
    (b'{"id":1', None), (b'{"id":1 ', None), (b'{"id":1,', 1),
    (b'{"id":1}', 1), (b'{"id":1e', None), (b'{"id":1.2,', None),
    (b'{"params":{"id":1},"id":12', None),
])
def test_oversized_prefix_requires_complete_top_level_integer_id(prefix, expected):
    assert response_id(prefix) == expected


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
