"""Exclusive control leases and viewer input for daemon-owned browser tabs."""

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from crewbots.browser_cdp import BrowserError
from crewbots.browser_input import input_event

CONTEXT_GRACE_S = 5
LEASE_S = 15
CONTROL_NOTICE = "The user controlled the page; it may have changed. Inspect it again."


@dataclass
class ControlLease:
    generation: int
    owner: str
    deadline: float
    timer: asyncio.Task | None = None
    keys: dict = field(default_factory=dict)
    buttons: dict = field(default_factory=dict)
    releasing: bool = False


class BrowserControl:
    async def control_state(self, task_id, policy):
        async with self._lock:
            tab = await self._control_tab(task_id, policy)
            return {"generation": tab.generation, "url": tab.url, "title": tab.title,
                    "control": "user" if tab.lease else "bot",
                    "lease_generation": tab.lease.generation if tab.lease else None, "transition": tab.control_transition}

    async def _control_tab(self, task_id, policy, generation=None):
        tab = self.sessions.get(task_id)
        if not tab or (generation is not None and tab.generation != generation):
            raise BrowserError("Browser session is closed or stale")
        active, network = policy()
        if not active or network != tab.network:
            await self._close_task(task_id)
            if not self.sessions:
                await self._stop()
            raise BrowserError("Browser task ended or network policy changed; open the page again")
        if tab.lease and tab.lease.deadline <= asyncio.get_running_loop().time():
            async with tab.control_lock:
                if tab.lease and tab.lease.deadline <= asyncio.get_running_loop().time():
                    await self._release(tab, "expired")
        return tab

    async def _close_control_tab(self, tab):
        # A bot action may hold the browser-wide lock while this tab is cleaned up.
        # Stop only after that action settles and after checking it did not open a tab.
        async def stop_when_empty():
            async with self._lock:
                if not self.sessions:
                    await self._stop()
        try:
            if self.sessions.get(tab.task_id) is tab:
                await self._close_task(tab.task_id)
        finally:
            task = asyncio.create_task(stop_when_empty())
            task.add_done_callback(self._idle_done)

    def _lease(self, tab, generation, owner):
        lease = tab.lease
        if (not lease or lease.releasing or lease.generation != generation or lease.owner != owner
                or lease.deadline <= asyncio.get_running_loop().time()):
            raise BrowserError("Browser control lease is stale or belongs to another viewer")
        return lease

    def _renew_timer(self, tab, lease):
        if lease.timer and lease.timer is not asyncio.current_task():
            lease.timer.cancel()
        lease.deadline = asyncio.get_running_loop().time() + LEASE_S
        lease.timer = asyncio.create_task(self._lease_expiry(tab, lease))
        lease.timer.add_done_callback(self._idle_done)

    async def _lease_expiry(self, tab, lease):
        await asyncio.sleep(max(0, lease.deadline - asyncio.get_running_loop().time()))
        if self.sessions.get(tab.task_id) is not tab or tab.lease is not lease:
            return
        lease.releasing = True
        async with tab.control_lock:
            if self.sessions.get(tab.task_id) is tab and tab.lease is lease:
                await self._release(tab, "expired")

    async def take_control(self, task_id, generation, policy):
        async with self._lock:
            tab = await self._control_tab(task_id, policy, generation)
            active, network = policy()  # expiry cleanup may have yielded to a policy change
            if self.sessions.get(task_id) is not tab or not active or network != tab.network:
                if self.sessions.get(task_id) is tab:
                    await self._close_task(task_id)
                if not self.sessions:
                    await self._stop()
                raise BrowserError("Browser task ended or network policy changed; open the page again")
            if tab.lease:
                raise BrowserError("The browser is already controlled by another viewer")
            self._lease_generation += 1
            lease = ControlLease(self._lease_generation, uuid4().hex, 0)
            tab.lease = lease
            tab.control_transition = "taken"
            self._renew_timer(tab, lease)
            return {"generation": tab.generation, "lease_generation": lease.generation, "owner": lease.owner}

    async def renew_control(self, task_id, generation, lease_generation, owner, policy):
        tab = self.sessions.get(task_id)
        if not tab or tab.generation != generation:
            raise BrowserError("Browser session is closed or stale")
        lease = self._lease(tab, lease_generation, owner)
        active, network = policy()
        if not active or network != tab.network:
            async with tab.control_lock:
                await self._close_control_tab(tab)
            raise BrowserError("Browser task ended or network policy changed; open the page again")
        self._renew_timer(tab, lease)

    async def release_control(self, task_id, generation, lease_generation, owner, policy):
        tab = self.sessions.get(task_id)
        if not tab or tab.generation != generation:
            raise BrowserError("Browser session is closed or stale")
        lease = self._lease(tab, lease_generation, owner)
        lease.releasing = True
        if lease.timer:
            lease.timer.cancel()
        cleanup = asyncio.create_task(self._finish_release(tab, lease, policy))
        cleanup.add_done_callback(self._idle_done)
        await asyncio.shield(cleanup)

    async def _finish_release(self, tab, lease, policy):
        async with tab.control_lock:
            if self.sessions.get(tab.task_id) is not tab or tab.lease is not lease:
                raise BrowserError("Browser session is closed or stale")
            active, network = policy()
            if not active or network != tab.network:
                await self._close_control_tab(tab)
                raise BrowserError("Browser task ended or network policy changed; open the page again")
            await self._release(tab)

    async def _release(self, tab, reason="handed back"):
        lease = tab.lease
        if lease.timer and lease.timer is not asyncio.current_task():
            lease.timer.cancel()
        # The lock fences queued input and keeps bot actions out until cleanup completes.
        try:
            for key in lease.keys.values():
                await self.cdp.call("Input.dispatchKeyEvent", {**key, "type": "keyUp"}, tab.session)
            for button in lease.buttons.values():
                await self.cdp.call("Input.dispatchMouseEvent", {**button, "type": "mouseReleased"}, tab.session)
        except BaseException:
            self._control_notices.append(tab.task_id)
            await self._close_control_tab(tab)
            raise
        tab.lease = None
        tab.notice = True
        tab.control_transition = reason

    async def control_input(self, task_id, generation, lease_generation, owner, policy, event):
        tab = self.sessions.get(task_id)
        if not tab or tab.generation != generation:
            raise BrowserError("Browser session is closed or stale")
        async with tab.control_lock:
            if self.sessions.get(task_id) is not tab:
                raise BrowserError("Browser session is closed or stale")
            lease = self._lease(tab, lease_generation, owner)
            active, network = policy()
            if not active or network != tab.network:
                await self._close_control_tab(tab)
                raise BrowserError("Browser task ended or network policy changed; open the page again")
            method, params = input_event(event, tab.viewport)
            kind = params.get("type")
            if method == "Input.dispatchKeyEvent":
                identity = params["code"]
                if kind in {"keyDown", "rawKeyDown"}:
                    lease.keys[identity] = {k: v for k, v in params.items() if k in {"key", "code", "windowsVirtualKeyCode"}}
            elif method == "Input.dispatchMouseEvent" and kind == "mousePressed":
                lease.buttons[params["button"]] = {k: v for k, v in params.items() if k in {"x", "y", "button"}}
            elif method == "Input.dispatchMouseEvent" and kind == "mouseMoved":
                for button in lease.buttons.values():
                    button.update(x=params["x"], y=params["y"])
            try:
                await self.cdp.call(method, params, tab.session)
            except BaseException:
                # A failed or cancelled dispatch has an unknown effect in Chrome.
                await self._close_control_tab(tab)
                raise
            if method == "Input.dispatchKeyEvent" and kind == "keyUp":
                lease.keys.pop(identity, None)
            if method == "Input.dispatchMouseEvent" and kind == "mouseReleased":
                lease.buttons.pop(params["button"], None)
            active, network = policy()
            if self.sessions.get(task_id) is not tab or not active or network != tab.network:
                await self._close_control_tab(tab)
                raise BrowserError("Browser task ended or network policy changed; open the page again")
            self._arm_idle(tab)

