"""The daemon's lease is the boundary between bot and viewer input."""

import asyncio

import httpx
import pytest
from fakes import bot, make_crew
from test_browser import browser as browser_fixture

from crewbots.api import create_app
from crewbots.browser import BrowserError
from crewbots.browser_input import input_event
from crewbots.runner import Runner
from crewbots.store import Status, Store

browser = browser_fixture


def policy():
    return True, False


KEY = {"type": "keyDown", "key": "Shift", "code": "ShiftLeft", "windowsVirtualKeyCode": 16}


async def opened(browser, tmp_path):
    service, fake = browser
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    return service, fake, service.sessions["a"].generation


def args(grant):
    return grant["generation"], grant["lease_generation"], grant["owner"], policy


async def test_generations_fence_old_commands_and_sessions(browser, tmp_path):
    service, fake, generation = await opened(browser, tmp_path)
    first = await service.take_control("a", generation, policy)
    with pytest.raises(BrowserError, match="already controlled"):
        await service.take_control("a", generation, policy)
    await service.release_control("a", *args(first))
    second = await service.take_control("a", generation, policy)
    assert second["lease_generation"] != first["lease_generation"]
    for operation in (service.renew_control("a", *args(first)), service.release_control("a", *args(first)),
                      service.control_input("a", *args(first), KEY)):
        with pytest.raises(BrowserError, match="stale"):
            await operation
    assert not [c for c in fake.calls if c["method"] == "Input.dispatchKeyEvent"]
    await service.close_task("a")
    await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    assert service.sessions["a"].generation != generation
    with pytest.raises(BrowserError, match="stale"):
        await service.take_control("a", generation, policy)


async def test_release_waits_for_dispatched_input_and_releases_before_bot(browser, tmp_path):
    service, fake, generation = await opened(browser, tmp_path)
    grant = await service.take_control("a", generation, policy)
    original = service.cdp.call
    started, resume = asyncio.Event(), asyncio.Event()

    async def blocked(method, params=None, session=None):
        if method == "Input.dispatchKeyEvent" and params["type"] == "keyDown":
            started.set()
            await resume.wait()
        return await original(method, params, session)

    service.cdp.call = blocked
    pending = asyncio.create_task(service.control_input("a", *args(grant), KEY))
    await started.wait()
    release = asyncio.create_task(service.release_control("a", *args(grant)))
    await asyncio.sleep(0)
    with pytest.raises(BrowserError, match="user has control"):
        await service.action("a", tmp_path, False, "console", {})
    resume.set()
    await pending
    await release
    result = await service.action("a", tmp_path, False, "console", {})
    assert "Inspect it again" in result[0]["text"]
    assert [c["params"]["type"] for c in fake.calls if c["method"] == "Input.dispatchKeyEvent"] == ["keyDown", "keyUp"]


async def test_release_fences_queued_input_and_survives_caller_cancellation(browser, tmp_path):
    service, fake, generation = await opened(browser, tmp_path)
    grant = await service.take_control("a", generation, policy)
    original = service.cdp.call
    started, resume = asyncio.Event(), asyncio.Event()

    async def blocked(method, params=None, session=None):
        if method == "Input.dispatchKeyEvent" and params["type"] == "keyDown":
            started.set()
            await resume.wait()
        return await original(method, params, session)

    service.cdp.call = blocked
    first = asyncio.create_task(service.control_input("a", *args(grant), KEY))
    await started.wait()
    release = asyncio.create_task(service.release_control("a", *args(grant)))
    await asyncio.sleep(0)
    delayed = asyncio.create_task(service.control_input("a", *args(grant), {"type": "text", "text": "late"}))
    release.cancel()
    with pytest.raises(asyncio.CancelledError):
        await release
    resume.set()
    await first
    with pytest.raises(BrowserError, match="stale"):
        await delayed
    # Let the shielded cleanup finish even though its HTTP caller disappeared.
    async with service.sessions["a"].control_lock:
        assert service.sessions["a"].lease is None
    assert not any(c["method"] == "Input.insertText" for c in fake.calls)
    assert any(c["method"] == "Input.dispatchKeyEvent" and c["params"]["type"] == "keyUp" for c in fake.calls)


async def test_mouse_button_released_on_expiry_and_idle_closes_control(browser, tmp_path, monkeypatch):
    monkeypatch.setattr("crewbots.browser_control.LEASE_S", 0.02)
    service, fake, generation = await opened(browser, tmp_path)
    grant = await service.take_control("a", generation, policy)
    await service.control_input("a", *args(grant), {"type": "mousePressed", "x": 12, "y": 20, "button": "left"})
    await asyncio.sleep(0.08)
    assert [c["params"]["type"] for c in fake.calls if c["method"] == "Input.dispatchMouseEvent"] == [
        "mousePressed", "mouseReleased"]
    service.idle_s = 0.02
    monkeypatch.setattr("crewbots.browser_control.LEASE_S", 1)
    grant = await service.take_control("a", generation, policy)
    await service.control_input("a", *args(grant), {"type": "text", "text": "x"})
    await asyncio.sleep(0.08)
    assert "a" not in service.sessions


async def test_renew_and_expiry_do_not_wait_for_another_tasks_stalled_action(browser, tmp_path, monkeypatch):
    monkeypatch.setattr("crewbots.browser_control.LEASE_S", 0.5)
    service, fake, generation = await opened(browser, tmp_path)
    await service.action("b", tmp_path, False, "open", {"url": "http://localhost"})
    grant = await service.take_control("a", generation, policy)
    await service.control_input("a", *args(grant), KEY)
    started, resume = asyncio.Event(), asyncio.Event()
    original = service.cdp.call
    other_session = service.sessions["b"].session

    async def blocked(method, params=None, session=None):
        if method == "Runtime.evaluate" and session == other_session:
            started.set()
            await resume.wait()
        return await original(method, params, session)

    service.cdp.call = blocked
    other = asyncio.create_task(service.action("b", tmp_path, False, "evaluate", {"expression": "1"}))
    await started.wait()
    try:
        await asyncio.sleep(0.3)
        await asyncio.wait_for(service.renew_control("a", *args(grant)), 0.1)
        await asyncio.sleep(0.25)  # past the original deadline
        assert service.sessions["a"].lease is not None
        await asyncio.sleep(0.3)  # past the renewed deadline
        assert service.sessions["a"].lease is None
        assert any(c["method"] == "Input.dispatchKeyEvent" and c["params"]["type"] == "keyUp" for c in fake.calls)
        assert not other.done()
    finally:
        resume.set()
        await other


async def test_key_identity_is_physical_code_and_drag_releases_at_last_position(browser, tmp_path):
    service, fake, generation = await opened(browser, tmp_path)
    grant = await service.take_control("a", generation, policy)
    events = [KEY, {"type": "keyDown", "key": "A", "code": "KeyA"},
              {"type": "keyUp", "key": "Shift", "code": "ShiftLeft"},
              {"type": "keyUp", "key": "a", "code": "KeyA"},
              {"type": "mousePressed", "x": 12, "y": 20, "button": "left"},
              {"type": "mouseMoved", "x": 40, "y": 50}]
    for event in events:
        await service.control_input("a", *args(grant), event)
    await service.release_control("a", *args(grant))
    keys = [c for c in fake.calls if c["method"] == "Input.dispatchKeyEvent"]
    assert len(keys) == 4  # no phantom KeyA or Shift key-up during cleanup
    released = [c["params"] for c in fake.calls if c["method"] == "Input.dispatchMouseEvent"][-1]
    assert released == {"type": "mouseReleased", "x": 40, "y": 50, "button": "left"}


@pytest.mark.parametrize("event", [
    {"type": "mouseMoved", "x": float("nan"), "y": 1},
    {"type": "mouseMoved", "x": float("inf"), "y": 1},
    {"type": "mouseMoved", "x": True, "y": 1},
    {"type": "mouseMoved", "x": 1, "y": 1, "extra": 1},
    {"type": "mousePressed", "x": 1, "y": 1},
    {"type": "keyDown", "key": "x" * 65, "code": "KeyX"},
    {"type": "text", "text": "x" * 4097},
])
def test_invalid_input_is_rejected(event):
    with pytest.raises(BrowserError, match="Invalid browser input"):
        input_event(event)


async def test_wheel_is_completed_and_known_viewport_is_enforced(browser, tmp_path):
    service, fake, generation = await opened(browser, tmp_path)
    await service.action("a", tmp_path, False, "screenshot", {"width": 320, "height": 240})
    grant = await service.take_control("a", generation, policy)
    await service.control_input("a", *args(grant), {"type": "mouseWheel", "x": 319, "y": 239, "deltaY": 4})
    wheel = [c["params"] for c in fake.calls if c["method"] == "Input.dispatchMouseEvent"][-1]
    assert wheel["deltaX"] == 0 and wheel["deltaY"] == 4
    for point in ({"x": 320, "y": 0}, {"x": 0, "y": 240}):
        with pytest.raises(BrowserError, match="Invalid"):
            await service.control_input("a", *args(grant), {"type": "mouseMoved", **point})
    assert service.sessions["a"].lease is not None


async def test_policy_is_rechecked_after_expired_control_cleanup(browser, tmp_path):
    service, _, generation = await opened(browser, tmp_path)
    grant = await service.take_control("a", generation, policy)
    await service.control_input("a", *args(grant), KEY)
    lease = service.sessions["a"].lease
    lease.timer.cancel()
    lease.deadline = 0
    current = [False]
    started, resume = asyncio.Event(), asyncio.Event()
    original = service.cdp.call

    async def blocked(method, params=None, session=None):
        if method == "Input.dispatchKeyEvent" and params["type"] == "keyUp":
            started.set()
            await resume.wait()
        return await original(method, params, session)

    service.cdp.call = blocked
    take = asyncio.create_task(service.take_control("a", generation, lambda: (True, current[0])))
    await started.wait()
    current[0] = True
    resume.set()
    with pytest.raises(BrowserError, match="policy changed"):
        await take
    assert "a" not in service.sessions


async def test_cleanup_failure_closes_session(browser, tmp_path):
    service, _, generation = await opened(browser, tmp_path)
    grant = await service.take_control("a", generation, policy)
    await service.control_input("a", *args(grant), KEY)
    original = service.cdp.call

    async def fail(method, params=None, session=None):
        if method == "Input.dispatchKeyEvent" and params["type"] == "keyUp":
            raise BrowserError("cleanup failed")
        return await original(method, params, session)

    service.cdp.call = fail
    with pytest.raises(BrowserError, match="cleanup failed"):
        await service.release_control("a", *args(grant))
    assert "a" not in service.sessions
    async with asyncio.timeout(1):
        while service.cdp is not None:
            await asyncio.sleep(0.01)
    assert service.cdp is None
    result = await service.action("a", tmp_path, False, "open", {"url": "http://localhost"})
    assert "Inspect it again" in result[0]["text"]


async def test_only_successful_input_resets_idle_and_policy_revocation_closes(browser, tmp_path):
    service, _, generation = await opened(browser, tmp_path)
    tab = service.sessions["a"]
    idle = tab.idle
    await service.control_state("a", policy)
    grant = await service.take_control("a", generation, policy)
    await service.renew_control("a", *args(grant))
    assert tab.idle is idle
    with pytest.raises(BrowserError, match="Invalid"):
        await service.control_input("a", *args(grant), {"type": "bad"})
    assert tab.idle is idle
    await service.control_input("a", *args(grant), {"type": "text", "text": "hi"})
    assert tab.idle is not idle
    with pytest.raises(BrowserError, match="policy changed"):
        await service.control_input("a", grant["generation"], grant["lease_generation"], grant["owner"],
                                    lambda: (True, True), KEY)
    assert "a" not in service.sessions


@pytest.mark.parametrize("failure", ["input", "release_policy"])
async def test_control_failure_stops_last_browser_session(browser, tmp_path, failure):
    service, _, generation = await opened(browser, tmp_path)
    grant = await service.take_control("a", generation, policy)
    if failure == "input":
        original = service.cdp.call

        async def fail(method, params=None, session=None):
            if method == "Input.insertText":
                raise BrowserError("dispatch failed")
            return await original(method, params, session)

        service.cdp.call = fail
        operation = service.control_input("a", *args(grant), {"type": "text", "text": "hello"})
    else:
        operation = service.release_control("a", grant["generation"], grant["lease_generation"], grant["owner"],
                                            lambda: (True, True))
    with pytest.raises(BrowserError):
        await operation
    async with asyncio.timeout(1):
        while service.cdp is not None:
            await asyncio.sleep(0.01)
    assert not service.sessions


async def test_expiry_releases_keys_and_bot_can_resume(browser, tmp_path, monkeypatch):
    monkeypatch.setattr("crewbots.browser_control.LEASE_S", 0.02)
    service, fake, generation = await opened(browser, tmp_path)
    grant = await service.take_control("a", generation, policy)
    await service.control_input("a", *args(grant), KEY)
    await asyncio.sleep(0.08)
    assert service.sessions["a"].lease is None
    assert any(c["method"] == "Input.dispatchKeyEvent" and c["params"]["type"] == "keyUp" for c in fake.calls)
    assert "Inspect it again" in (await service.action("a", tmp_path, False, "console", {}))[0]["text"]


async def test_takeover_waits_for_bot_action(browser, tmp_path):
    service, _, generation = await opened(browser, tmp_path)
    original = service._perform
    started, resume = asyncio.Event(), asyncio.Event()

    async def blocked(*args):
        started.set()
        await resume.wait()
        return await original(*args)

    service._perform = blocked
    action = asyncio.create_task(service.action("a", tmp_path, False, "console", {}))
    await started.wait()
    take = asyncio.create_task(service.take_control("a", generation, policy))
    await asyncio.sleep(0)
    assert not take.done()
    resume.set()
    await action
    await take


async def test_user_api_is_authenticated_and_rechecks_task_status(browser, tmp_path):
    service, _, generation = await opened(browser, tmp_path)
    store = Store(":memory:")
    crew = make_crew(tmp_path, bot("a"))
    runner = Runner(store, crew)
    runner.browser = service
    task = store.create_task("a", "test", status=Status.RUNNING)
    service.sessions[task.id] = service.sessions.pop("a")
    service.sessions[task.id].task_id = task.id
    app = create_app(runner, "secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8765") as client:
        url = f"/v1/tasks/{task.id}/browser"
        assert (await client.get(url)).status_code == 403
        response = await client.post(url + "/take", headers={"Authorization": "Bearer secret"}, json={"generation": generation})
        assert response.status_code == 200
        store.update_task(task.id, status=Status.CANCELLED)
        response = await client.post(url + "/renew", headers={"Authorization": "Bearer secret"}, json=response.json())
        assert response.status_code == 409
        assert task.id not in service.sessions
    store.close()


async def test_input_and_release_api_validate_bodies_and_lease(browser, tmp_path):
    service, fake, generation = await opened(browser, tmp_path)
    store = Store(":memory:")
    runner = Runner(store, make_crew(tmp_path, bot("a")))
    runner.browser = service
    task = store.create_task("a", "test", status=Status.RUNNING)
    service.sessions[task.id] = service.sessions.pop("a")
    service.sessions[task.id].task_id = task.id
    app = create_app(runner, "secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:8765",
                                 headers={"Authorization": "Bearer secret"}) as client:
        url = f"/v1/tasks/{task.id}/browser"
        grant = (await client.post(url + "/take", json={"generation": generation})).json()
        event = {"type": "text", "text": "hello"}
        assert (await client.post(url + "/input", json={**grant, "event": event, "extra": 1})).status_code == 422
        assert (await client.post(url + "/release", json={**grant, "extra": 1})).status_code == 422
        assert (await client.post(url + "/input", json={**grant, "event": event})).status_code == 200
        assert any(c["method"] == "Input.insertText" and c["params"] == {"text": "hello"} for c in fake.calls)
        assert (await client.post(url + "/release", json=grant)).status_code == 200
        assert (await client.post(url + "/input", json={**grant, "event": event})).status_code == 409
    store.close()
