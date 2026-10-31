"""Daemon-owned Chrome, one disposable context per task. No CLI sandbox is widened.

Sessions are the single source for tab identity, URL and title; a future app pane can
subscribe to these same CDP sessions for screencast frames and user input.
"""

import asyncio
import base64
import contextlib
import json
import logging
import os
import shutil
import tempfile
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from crewbots import paths
from crewbots.browser_cdp import CDP, BrowserError
from crewbots.browser_control import CONTROL_NOTICE, BrowserControl, ControlLease
from crewbots.browser_process import LaunchStillRunning, Profiles, find_chrome, launch
from crewbots.browser_proxy import BrowserProxy, Network, allowed

log = logging.getLogger(__name__)

CONTEXT_GRACE_S = 5


@dataclass
class BrowserSession:
    task_id: str
    context: str
    target: str
    session: str
    network: Network
    proxy: BrowserProxy
    url: str = "about:blank"
    title: str = ""
    console: deque = field(default_factory=lambda: deque(maxlen=200))
    loaded: asyncio.Event = field(default_factory=asyncio.Event)
    auth_requests: deque = field(default_factory=lambda: deque(maxlen=200))
    idle: asyncio.Task | None = None
    failure: str | None = None
    generation: int = 0
    lease: ControlLease | None = None
    notice: bool = False
    control_transition: str | None = None
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    viewport: tuple[int, int] | None = None


class TargetCloseError(BrowserError):
    def __init__(self, tab, error):
        super().__init__(str(error))
        self.tab = tab


class Browser(BrowserControl):
    def __init__(self, root: Path | None = None, *, idle_s: float = 900):
        self.root = (root or paths.root() / "browser").absolute()
        self.idle_s = idle_s
        self.sessions: dict[str, BrowserSession] = {}
        self.cdp: CDP | None = None
        self.process = None
        self.profile = None
        self._leave_profile = False
        # A custom root can be bot-writable (the manual smoke check); never trust owner records there.
        self._profiles = Profiles(self.root, sweep=root is None)
        self._startup_targets: set[str] = set()
        self._lock = asyncio.Lock()
        self._active: asyncio.Task | None = None
        self._closing = False
        # A new daemon must not reuse a generation from its predecessor.
        self._session_generation = uuid4().int >> 88  # stays exact as a JavaScript number
        self._lease_generation = 0
        self._control_notices = deque(maxlen=256)
        self._log = None
        self._creating: dict[str, asyncio.Future] = {}
        self._disposing: dict[str, BrowserSession] = {}
        self._context_timers: dict[str, asyncio.TimerHandle] = {}
        self._gone: dict[str, str] = {}  # why a task's page closed, for the call that finds it gone
        self._stderr = ""
        self._last_cdp: dict = {}
        self._proxy_logs: dict[str, deque] = {}

    async def _start(self):
        if self.cdp and self.process.returncode is None and not self.cdp.reader.done():
            return
        await self._stop()
        chrome = find_chrome()
        try:
            await self._profiles.acquire()
            self.profile = Path(tempfile.mkdtemp(prefix="chrome-", dir=self.root))
            self._log = open(self.profile / "chrome.log", "wb")
            self.process, transport = await launch([
                chrome, "--headless=new", "--remote-debugging-pipe",
                f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
                "--disable-background-networking", "--disable-component-update", "--disable-sync",
                "--disable-domain-reliability", "--disable-extensions", "--disable-quic",
                "--disable-features=MediaRouter,PreconnectToSearch,NetworkTimeServiceQuerying",
                # NetworkContext proxy overrides are not restored after a network service restart.
                "--enable-features=NetworkServiceInProcess2",
                "--disable-breakpad", "--disable-crash-reporter",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1",
                "--password-store=basic", "--use-mock-keychain", "about:blank",
            ], self._log, self.profile)
            self.cdp = CDP(transport)
            self.cdp.on_event = self._event
            self.cdp.on_event_error = self._event_error
            initial = await self.cdp.call("Target.getTargets")
            self._startup_targets = {target["targetId"] for target in initial["targetInfos"]
                                     if target.get("type") == "page"}
            await self.cdp.call("Browser.setDownloadBehavior", {"behavior": "deny"})
            await self.cdp.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True,
                                                        "flatten": True, "filter": [{"type": "page"}]})
        except BaseException as error:
            if isinstance(error, LaunchStillRunning):
                self._leave_profile = True
            await self._stop()
            raise

    async def _session(self, task_id, network):
        await self._start()
        if tab := self.sessions.get(task_id):
            return tab
        proxy = BrowserProxy(network)
        address = await proxy.start()
        if len(self._proxy_logs) >= 16:
            self._proxy_logs.pop(next(iter(self._proxy_logs)))
        self._proxy_logs[task_id] = proxy.log
        # No page may reach a different task's proxy, even with its own proxy credentials.
        for other in self.sessions.values():
            other.proxy.blocked_ports.update(proxy.blocked_ports)
            proxy.blocked_ports.update(other.proxy.blocked_ports)
        context = None
        try:
            context = (await self.cdp.call("Target.createBrowserContext", {
                "disposeOnDetach": True, "proxyServer": address, "proxyBypassList": "<-loopback>"}))["browserContextId"]
            await self.cdp.call("Browser.setDownloadBehavior", {"behavior": "deny", "browserContextId": context})
            # Adopt the auto-attached debugger. A second attach leaves the first session
            # waiting for Runtime.runIfWaitingForDebugger and can stall Page.navigate.
            attached = asyncio.get_running_loop().create_future()
            self._creating[context] = attached
            target = (await self.cdp.call("Target.createTarget", {"url": "about:blank", "browserContextId": context}))["targetId"]
            try:
                async with asyncio.timeout(10):
                    attached_target, session = await attached
            except TimeoutError:
                raise BrowserError(f"Chrome timed out: Target.attachedToTarget for target {target}, context {context}") from None
            if attached_target != target:
                raise BrowserError(f"Unexpected auto-attached target {attached_target}; expected {target}")
            self._session_generation += 1
            tab = BrowserSession(task_id, context, target, session, network, proxy, generation=self._session_generation)
            self.sessions[task_id] = tab
            self._gone.pop(task_id, None)
            await self._configure(session)
            await self.cdp.call("Runtime.runIfWaitingForDebugger", session=session)
            self._arm_idle(tab)
            return tab
        except BaseException:
            if context:
                with contextlib.suppress(Exception):
                    await self.cdp.call("Target.disposeBrowserContext", {"browserContextId": context})
            self.sessions.pop(task_id, None)
            await proxy.close()
            raise
        finally:
            self._creating.pop(context, None)

    async def _configure(self, session):
        for method, params in (
            ("Page.enable", {}), ("Runtime.enable", {}), ("Log.enable", {}), ("Inspector.enable", {}),
            ("Page.setInterceptFileChooserDialog", {"enabled": True}),
            ("Network.enable", {}), ("Network.setBypassServiceWorker", {"bypass": True}),
            ("Fetch.enable", {"patterns": [{"urlPattern": "*"}], "handleAuthRequests": True}),
            ("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}),
        ):
            await self.cdp.call(method, params, session)

    async def _close_target(self, target):
        try:
            result = await self.cdp.call("Target.closeTarget", {"targetId": target})
        except BrowserError as error:
            if str(error) == "No target with given id found":
                return  # A popup may have closed itself before the command arrived.
            raise
        if result.get("success") is False:
            raise BrowserError("Target.closeTarget returned false")

    def _context_owner(self, context):
        return next((t for t in self.sessions.values() if context and t.context == context), None) or self._disposing.get(context)

    async def _event(self, message):
        method, data, session = message.get("method"), message.get("params", {}), message.get("sessionId")
        tab = next((t for t in self.sessions.values() if t.session == session), None)
        if method == "Target.attachedToTarget":
            info = data["targetInfo"]
            attached = self._creating.get(info.get("browserContextId"))
            if not session and info["targetId"] in self._startup_targets:
                # Keep the initial page, but fail all its requests: it has no task proxy.
                try:
                    await self.cdp.call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}, data["sessionId"])
                    await self.cdp.call("Runtime.runIfWaitingForDebugger", session=data["sessionId"])
                except Exception:
                    # Close an incompletely configured target; if that fails, stop Chrome.
                    await self._close_target(info["targetId"])
                    self._startup_targets.discard(info["targetId"])
                    return
                self._startup_targets.discard(info["targetId"])
            elif not session and attached is not None and not attached.done() and info.get("url") == "about:blank":
                attached.set_result((info["targetId"], data["sessionId"]))
            else:
                # Unrequested targets, including about:blank popups, must never stay paused indefinitely.
                owner = self._context_owner(info.get("browserContextId")) if not session and info.get("type") == "page" else None
                try:
                    await self._close_target(info["targetId"])
                except Exception as e:
                    if not session and info.get("type") == "page":
                        if not owner:
                            # An unknown context may be the unprotected default context.
                            self._startup_targets.add(info["targetId"])
                            raise
                        raise TargetCloseError(owner, e) from e
                    # A raced or non-closeable child is still paused. Do not discard its parent task.
                    log.warning("Could not close browser target %s: %s", info["targetId"], e)
                    self.cdp.note("target_close_error", targetId=info["targetId"], session=session, error=str(e))
            return
        if method in {"Target.detachedFromTarget", "Inspector.targetCrashed"}:
            lost = data.get("sessionId") if method == "Target.detachedFromTarget" else session
            if mine := next((t for t in self.sessions.values() if t.session == lost), None):
                await self._lost(mine, "it crashed" if method == "Inspector.targetCrashed" else "Chrome closed it")
            return
        if method == "Fetch.authRequired":
            challenge = data["authChallenge"]
            request_id = data["requestId"]
            provide = (tab is not None and challenge.get("source") == "Proxy"
                       and challenge.get("scheme", "").lower() == "basic"
                       and challenge.get("origin", "").rstrip("/") == tab.proxy.address
                       and challenge.get("realm") == tab.proxy.realm and request_id not in tab.auth_requests)
            response = {"response": "CancelAuth"}
            if provide:
                tab.auth_requests.append(request_id)
                response = {"response": "ProvideCredentials", "username": tab.proxy.username, "password": tab.proxy.password}
            await self.cdp.call("Fetch.continueWithAuth", {"requestId": request_id, "authChallengeResponse": response}, session)
            return
        if method == "Fetch.requestPaused":
            url = data["request"]["url"]
            permit = tab is not None and (allowed(url, tab.network) or url.startswith(("data:", "blob:")))
            # Even an unknown/detached session gets an explicit failure, not a stranded request.
            await self.cdp.call("Fetch.continueRequest" if permit else "Fetch.failRequest",
                                {"requestId": data["requestId"],
                                 **({} if permit else {"errorReason": "BlockedByClient"})}, session)
            if not permit and tab:
                tab.console.append({"type": "blocked", "text": f"Blocked request: {url}"})
            return
        if not tab:
            return
        if method == "Page.frameNavigated" and not data["frame"].get("parentId"):
            tab.url = data["frame"]["url"]
        elif method == "Page.loadEventFired":
            tab.loaded.set()
        elif method == "Runtime.consoleAPICalled":
            tab.console.append({"type": data["type"], "text": " ".join(
                str(a.get("value", a.get("description", ""))) for a in data.get("args", []))[:8000]})
        elif method == "Runtime.exceptionThrown":
            detail = data["exceptionDetails"]
            text = str(detail.get("exception", {}).get("description", detail["text"]))[:8000]
            tab.console.append({"type": "error", "text": text})
        elif method == "Log.entryAdded":
            tab.console.append({"type": data["entry"]["level"], "text": data["entry"]["text"][:8000]})
        elif method == "Page.javascriptDialogOpening":
            await self.cdp.call("Page.handleJavaScriptDialog", {"accept": False}, session)

    async def _event_error(self, message, error):
        session = message.get("sessionId")
        info = message.get("params", {}).get("targetInfo", {})
        target = info.get("targetId")
        unsafe_startup = not session and target in self._startup_targets
        if unsafe_startup:
            # We could neither protect nor close the default target. Stop every proxy
            # and break pending calls before waiting for the action lock.
            for other in list(self.sessions.values()):
                await other.proxy.close()
            self.cdp.transport.abort()
            if self.process and self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
        tab = next((t for t in self.sessions.values() if t.session == session), None)
        if not tab and not unsafe_startup and not session and message.get("method") == "Target.attachedToTarget":
            tab = error.tab if isinstance(error, TargetCloseError) else self._context_owner(info.get("browserContextId"))
        if tab:
            # Stop network traffic and release a pending navigation before waiting for the action lock.
            tab.failure = f"Chrome event handler failed: {error}"
            tab.loaded.set()
            self.cdp.fail_session(tab.session, tab.failure)
            await tab.proxy.close()
        async with self._lock:
            if unsafe_startup:
                await self._stop()
                return
            if tab and self.sessions.get(tab.task_id) is tab:
                self._went(tab.task_id, tab.failure)
                await self._close_task(tab.task_id)  # fail closed for this context only
            if not self.sessions:
                await self._stop()

    async def _evaluate(self, tab, expression):
        try:
            result = await self.cdp.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                             "awaitPromise": True, "timeout": 10000}, tab.session)
        except BrowserError as error:
            if not str(error).startswith("Chrome timed out"):
                raise
            raise BrowserError(f"The script was still going after {self.cdp.timeout_s:g} s, so its answer was given up "
                               "on: keep each one short, and wait between calls rather than inside one") from None
        if result.get("exceptionDetails"):
            raise BrowserError(str(result["exceptionDetails"].get("exception", {}).get("description", "JavaScript failed")))
        return result.get("result", {}).get("value")

    async def action(self, task_id: str, folder: Path, network: Network, action: str, args: dict) -> list[dict]:
        tab = self.sessions.get(task_id)
        if tab and tab.lease and tab.network == network:
            raise BrowserError("The user has control of the browser; wait for hand-back")
        async with self._lock:
            if self._closing:
                raise BrowserError("The browser service is shutting down")
            self._active = asyncio.current_task()
            try:
                return await self._action_locked(task_id, folder, network, action, args)
            finally:
                self._active = None

    async def _action_locked(self, task_id, folder, network, action, args):
        # Revocation applies before even an invalid/denied open. It must not leave a
        # previous network=true context sending background requests under the old grant.
        tab = self.sessions.get(task_id)
        if tab and tab.network != network:
            await self._close_task(task_id)
            tab = None
        if tab and tab.lease:
            raise BrowserError("The user has control of the browser; wait for hand-back")
        try:
            if action == "open":
                if not allowed(args["url"], network) or urlsplit(args["url"]).scheme not in {"http", "https"}:
                    raise BrowserError("URL is not allowed by this bot's network setting; only HTTP(S) pages are supported")
                tab = await self._session(task_id, network)
                try:
                    content = await self._perform(tab, folder, action, args)
                except BrowserError as error:
                    if "Session with given id not found" not in str(error):
                        raise
                    await self._close_task(task_id)  # Chrome dropped the page without a word: a new one, once
                    tab = await self._session(task_id, network)
                    content = await self._perform(tab, folder, action, args)
            elif (not tab or not self.cdp or self.cdp.reader.done()
                  or urlsplit(tab.url).scheme not in {"http", "https"}):
                why = self._gone.get(task_id)
                raise BrowserError("Open an HTTP(S) page first" + (f": the last one went because {why}" if why else
                                   "; the browser may have closed after being idle"))
            else:
                content = await self._perform(tab, folder, action, args)
            self._arm_idle(tab)
            if tab.notice or task_id in self._control_notices:
                tab.notice = False
                self._control_notices = deque((t for t in self._control_notices if t != task_id), maxlen=256)
                content.insert(0, {"type": "text", "text": CONTROL_NOTICE})
            return content
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise BrowserError(str(e)) from None
        finally:
            if not self.sessions:
                await self._stop()

    async def _perform(self, tab, folder, action, args):
        if tab.failure:
            raise BrowserError(tab.failure)
        if action == "open":
            tab.loaded.clear()
            since = tab.proxy.log[-1] if tab.proxy.log else None
            try:
                result = await self.cdp.call("Page.navigate", {"url": args["url"]}, tab.session)
                if result.get("errorText"):
                    raise BrowserError(result["errorText"])
                async with asyncio.timeout(10):
                    await tab.loaded.wait()
                if tab.failure:
                    raise BrowserError(tab.failure)
            except (TimeoutError, BrowserError) as error:
                # The bot gets the reason; the full proxy and CDP logs stay in diagnostics().
                reason = str(error) if isinstance(error, BrowserError) else f"{args['url']} didn't finish loading in 10 s"
                if refusal := tab.proxy.refusal(since):
                    reason += f"; {refusal}"
                raise BrowserError(reason) from None
        elif action == "screenshot":
            width, height = args.get("width", 1400), args.get("height", 860)
            if type(width) is not int or type(height) is not int or not (320 <= width <= 3840 and 240 <= height <= 2160):
                raise BrowserError("viewport must be 320–3840 × 240–2160")
            theme = args.get("theme", "light")
            if theme not in {"light", "dark"}:
                raise BrowserError("theme must be light or dark")
            await self.cdp.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1, "mobile": False}, tab.session)
            tab.viewport = (width, height)
            await self.cdp.call("Emulation.setEmulatedMedia", {
                "features": [{"name": "prefers-color-scheme", "value": theme}]}, tab.session)
            await self._evaluate(tab, "document.fonts.ready.then(() => new Promise(r => "
                                 "requestAnimationFrame(() => requestAnimationFrame(r))))")
            params = {"format": "png", "captureBeyondViewport": bool(args.get("full_page"))}
            if args.get("full_page"):
                size = (await self.cdp.call("Page.getLayoutMetrics", session=tab.session))["cssContentSize"]
                if size["width"] * size["height"] > 32_000_000:
                    raise BrowserError("page is too large for a full-page screenshot; use the viewport")
                params["clip"] = {**size, "scale": 1}
            shot = await self.cdp.call("Page.captureScreenshot", params, tab.session)
            # A generated name and O_EXCL/O_NOFOLLOW prevent a workspace symlink from redirecting daemon writes.
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"browser-{theme}-{uuid4().hex}.png"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(base64.b64decode(shot["data"], validate=True))
            return [{"type": "text", "text": str(path)}, {"type": "image", "mimeType": "image/png", "data": shot["data"]}]
        elif action == "click":
            selector = json.dumps(args["selector"])
            point = await self._evaluate(tab, f"""(() => {{ const e = document.querySelector({selector});
                if (!e) throw Error('No element matches');
                if (e.closest('input[type=file]')) throw Error('File uploads are disabled');
                e.scrollIntoView({{block:'center'}}); const r = e.getBoundingClientRect();
                return {{x:r.x+r.width/2, y:r.y+r.height/2}}; }})()""")
            for event in ("mousePressed", "mouseReleased"):
                await self.cdp.call("Input.dispatchMouseEvent",
                                    {"type": event, "button": "left", "clickCount": 1, **point}, tab.session)
        elif action == "type":
            if args.get("selector"):
                await self._evaluate(tab, f"""(() => {{ const e = document.querySelector({json.dumps(args['selector'])});
                    if (!e || e.type === 'file') throw Error('No editable element matches'); e.focus(); }})()""")
            if "text" in args:
                await self.cdp.call("Input.insertText", {"text": args["text"]}, tab.session)
            if args.get("key"):
                keys = {"Enter": 13, "Tab": 9, "Escape": 27, "Backspace": 8, "Delete": 46,
                        "ArrowLeft": 37, "ArrowUp": 38, "ArrowRight": 39, "ArrowDown": 40}
                key = args["key"]
                if key not in keys:
                    raise BrowserError("key must be one of " + ", ".join(keys))
                for event in ("keyDown", "keyUp"):
                    await self.cdp.call("Input.dispatchKeyEvent", {"type": event, "key": key,
                                        "windowsVirtualKeyCode": keys[key],
                                        **({"text": "\r"} if key == "Enter" and event == "keyDown" else {})},
                                        tab.session)
        elif action == "evaluate":
            value = await self._evaluate(tab, args["expression"])
            return [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]
        elif action == "console":
            messages = list(tab.console)
            if args.get("clear"):
                tab.console.clear()
            return [{"type": "text", "text": json.dumps(messages, ensure_ascii=False)}]
        else:
            raise BrowserError(f"Unknown browser action: {action}")
        state = await self._evaluate(tab, "({url: location.href, title: document.title})")
        tab.url, tab.title = state["url"], state["title"]
        return [{"type": "text", "text": json.dumps(state)}]

    def _arm_idle(self, tab):
        if tab.idle:
            tab.idle.cancel()
        tab.idle = asyncio.create_task(self._expire(tab, self.idle_s))
        tab.idle.add_done_callback(self._idle_done)

    async def _expire(self, tab, delay):
        try:
            await asyncio.sleep(delay)
            async with self._lock:
                if self.sessions.get(tab.task_id) is tab:
                    self._went(tab.task_id, f"no browser call came for {delay / 60:g} minutes")
                    await self._close_task(tab.task_id)
                if not self.sessions:
                    await self._stop()
        except Exception:
            log.exception("Could not expire browser context for task %s", tab.task_id)
            async with self._lock:
                if self.sessions.get(tab.task_id) is tab:
                    self.sessions.pop(tab.task_id)
                with contextlib.suppress(Exception):
                    await self._dispose_tab(tab)
                if not self.sessions:
                    try:
                        await self._stop()
                    except Exception:
                        log.exception("Could not stop browser after task %s expired", tab.task_id)

    def _went(self, task_id, why):
        self._gone[task_id] = why
        if len(self._gone) > 64:
            self._gone.pop(next(iter(self._gone)))

    async def _lost(self, tab, why):
        """A page Chrome closed or that crashed: its tab goes, so the next open makes a new one rather than calling a
        session that's gone, and the call that finds it gone says why."""
        tab.failure = f"Chrome lost this task's page: {why}"
        tab.loaded.set()
        self.cdp.fail_session(tab.session, tab.failure)
        async with self._lock:
            if self.sessions.get(tab.task_id) is tab:
                self._went(tab.task_id, why)
                await self._close_task(tab.task_id)
            if not self.sessions:
                await self._stop()

    @staticmethod
    def _idle_done(task):
        if not task.cancelled() and (error := task.exception()):
            log.error("Unexpected browser idle task failure", exc_info=(type(error), error, error.__traceback__))

    async def _close_task(self, task_id):
        tab = self.sessions.pop(task_id, None)
        if tab:
            if tab.lease and tab.lease.timer and tab.lease.timer is not asyncio.current_task():
                tab.lease.timer.cancel()
            tab.lease = None
            if tab.idle and tab.idle is not asyncio.current_task():
                tab.idle.cancel()
            await self._dispose_tab(tab)

    async def _dispose_tab(self, tab):
        if timer := self._context_timers.pop(tab.context, None):
            timer.cancel()
        self._disposing[tab.context] = tab
        try:
            try:
                await tab.proxy.close()  # stop its traffic even if CDP disposal races a detached session
            finally:
                if self.cdp:
                    with contextlib.suppress(Exception):
                        await self.cdp.call("Target.disposeBrowserContext", {"browserContextId": tab.context})
        finally:
            # Keep late events attributable after disposal, without retaining old contexts indefinitely.
            def forget():
                if self._disposing.get(tab.context) is tab:
                    self._disposing.pop(tab.context, None)
                    self._context_timers.pop(tab.context, None)
            self._context_timers[tab.context] = asyncio.get_running_loop().call_later(CONTEXT_GRACE_S, forget)

    async def close_task(self, task_id):
        async with self._lock:
            await self._close_task(task_id)
            if not self.sessions:
                await self._stop()

    async def _stop(self):
        for task_id in list(self.sessions):
            await self._close_task(task_id)
        if self.cdp:
            self._last_cdp = self.cdp.diagnostics()
            await self.cdp.close()
            self.cdp = None
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
            try:
                async with asyncio.timeout(1):
                    await self.process.wait()
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    self.process.kill()
                async with asyncio.timeout(1):
                    await self.process.wait()
        self.process = None
        if self._log:
            self._log.close()
            self._log = None
        if self.profile:
            self._stderr = self.chrome_stderr()
            if self._leave_profile:
                log.warning("Leaving browser profile %s because its launcher may still be running", self.profile)
            else:
                shutil.rmtree(self.profile)
            self.profile = None
            self._leave_profile = False
        self._profiles.close()
        self._startup_targets.clear()
        for timer in self._context_timers.values():
            timer.cancel()
        self._context_timers.clear()
        self._disposing.clear()

    def chrome_stderr(self) -> str:
        if self.profile:
            log = self.profile / "chrome.log"
            if log.exists():
                with log.open("rb") as stream:
                    stream.seek(max(0, log.stat().st_size - 65536))
                    return stream.read().decode("utf-8", errors="replace")
        return self._stderr

    def diagnostics(self) -> dict:
        return {
            "chrome_stderr": self.chrome_stderr(),
            "cdp": self.cdp.diagnostics() if self.cdp else self._last_cdp,
            "proxy": {task: list(log) for task, log in self._proxy_logs.items()},
            "sessions": {task: {"target": tab.target, "session": tab.session, "url": tab.url, "title": tab.title}
                         for task, tab in self.sessions.items()},
            "creating_contexts": list(self._creating),
        }

    async def close(self):
        # Shutdown must interrupt even a command blocked while holding the action lock.
        self._closing = True
        if self._active and self._active is not asyncio.current_task():
            self._active.cancel()
        if self.cdp:
            self.cdp.transport.abort()
        if self.process and self.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self.process.terminate()
        async with self._lock:
            await self._stop()
