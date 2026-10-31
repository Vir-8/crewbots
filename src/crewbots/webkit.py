"""A page in WebKit, Safari's engine, for a bot's webkit_check tool. WebKit's helper processes can't start
in a bot's sandbox, so the daemon runs the check for it, as it runs Chrome for the browser tools.

The helper is verify-webkit.swift (what `scripts/verify webkit` runs), compiled into crewbots' temp folder, which no bot
can write: a bot can't change what the daemon runs. Only local pages, and the helper blocks whatever they'd load from
anywhere else (--local-only). One check runs at a time, each with a time limit, and everything it writes (the helper,
the scripts it runs, WebKit's own temp files) stays in that folder.
"""

import asyncio
import hashlib
import os
import tempfile
from asyncio.subprocess import DEVNULL, PIPE
from pathlib import Path
from urllib.parse import urlsplit

from crewbots import paths

SOURCE = Path(__file__).with_name("verify-webkit.swift")
LOCAL = {"localhost", "127.0.0.1"}
MAX_WAIT_S = 10
TIMEOUT_S = 30  # the helper's own limit, after the wait: for the page to load and its scripts to run
GRACE_S = 10  # past the helper's own limit, before it's stopped: for starting up
BUILD_TIMEOUT_S = 300  # the first build makes a module cache for AppKit and WebKit, about a minute
OUTPUT_BYTES = 20_000


class WebKitError(Exception):
    """Why a check didn't run, or what went wrong in the page. The message goes back to the bot."""


class WebKit:
    def __init__(self, root: Path | None = None, swiftc: str = "swiftc"):
        self.root = root or paths.temp() / "webkit"
        self.swiftc = swiftc
        self.lock = asyncio.Lock()

    def helper(self) -> Path:
        """Where the helper built from the current source is, or goes."""
        return self._helper_for(SOURCE.read_bytes())

    def _helper_for(self, source: bytes) -> Path:
        return self.root / f"verify-webkit-{hashlib.sha256(source).hexdigest()[:16]}"

    async def check(self, url: str, *, click: str | None = None, setup: str | None = None, js: str | None = None,
                    wait: float = 2, width: int = 1400, height: int = 860) -> str:
        """Opens `url`, runs `setup`, clicks `click` with real mouse events, runs `js`, and returns what `js` returns
        (the page's title without one) as the helper prints it."""
        try:
            parsed = urlsplit(url)
            local = parsed.scheme in {"http", "https"} and parsed.hostname in LOCAL and "@" not in parsed.netloc
        except (ValueError, TypeError, AttributeError):
            local = False
        if not local:
            raise WebKitError("Only local pages: an http(s) URL on localhost or 127.0.0.1")
        if any(value is not None and not isinstance(value, str) for value in (click, setup, js)):
            raise WebKitError("click, setup and js are text")
        if type(wait) not in (int, float) or not 0 <= wait <= MAX_WAIT_S:
            raise WebKitError(f"wait must be 0 to {MAX_WAIT_S} seconds")
        if type(width) is not int or type(height) is not int or not (320 <= width <= 3840 and 240 <= height <= 2160):
            raise WebKitError("the window must be 320–3840 × 240–2160")
        if self.lock.locked():
            raise WebKitError("Another WebKit check is running, and one runs at a time (the first also builds the "
                              "helper, about a minute): try again shortly")
        async with self.lock:
            helper = await self._build()
            with tempfile.TemporaryDirectory(prefix="run-", dir=self.root) as work:
                args = [str(helper), url, "--local-only", "--size", f"{width}x{height}", "--wait", f"{wait:g}",
                        "--timeout", str(TIMEOUT_S)]
                for name, body in (("setup", setup), ("js", js)):
                    if body:
                        (Path(work) / f"{name}.js").write_text(body)
                        args += [f"--{name}", str(Path(work) / f"{name}.js")]
                if click:
                    args += ["--click", click]
                code, out, err = await self._run(args, work, wait + TIMEOUT_S + GRACE_S, "WebKit")
        if code != 0:
            raise WebKitError(err.strip() or f"the WebKit helper exited with {code}")
        return out.strip()

    async def _build(self) -> Path:
        """The helper for the current source, compiled the first time it's asked for."""
        source = SOURCE.read_bytes()
        helper = self._helper_for(source)
        if helper.is_file():
            return helper
        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="build-", dir=self.root) as work:
            copy, built = Path(work) / "verify-webkit.swift", Path(work) / "verify-webkit"
            copy.write_bytes(source)  # what was hashed is what's compiled
            try:
                code, _, err = await self._run([self.swiftc, "-O", str(copy), "-o", str(built), "-framework", "WebKit",
                                                "-framework", "AppKit", "-module-cache-path",
                                                str(self.root / "module-cache")], work, BUILD_TIMEOUT_S, "swiftc")
            except FileNotFoundError:
                raise WebKitError("swiftc isn't installed: the WebKit check needs Xcode's command line tools "
                                  "(xcode-select --install)") from None
            if code != 0 or not built.is_file():
                raise WebKitError(f"couldn't build the WebKit helper: {err.strip()[-2000:]}")
            os.replace(built, helper)
        for old in self.root.glob("verify-webkit-*"):
            if old != helper:
                old.unlink(missing_ok=True)
        return helper

    async def _run(self, args: list[str], work: str, limit_s: float, what: str) -> tuple[int, str, str]:
        """Runs `args` in `work`, which is also its TMPDIR; kills it after `limit_s` seconds."""
        process = await asyncio.create_subprocess_exec(*args, cwd=work, env={**os.environ, "TMPDIR": work},
                                                       stdin=DEVNULL, stdout=PIPE, stderr=PIPE)
        try:
            async with asyncio.timeout(limit_s):
                out, err = await asyncio.gather(_read(process.stdout), _read(process.stderr))
                await process.wait()
        except TimeoutError:
            raise WebKitError(f"{what} didn't finish in {limit_s:g} seconds, so it was stopped") from None
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return process.returncode, out, err


async def _read(stream: asyncio.StreamReader) -> str:
    """A stream to its end, keeping the first OUTPUT_BYTES: the rest is read and dropped, so the process never blocks
    on a full pipe."""
    kept, cut = bytearray(), False
    while chunk := await stream.read(65536):
        cut |= len(kept) + len(chunk) > OUTPUT_BYTES
        kept += chunk[:max(0, OUTPUT_BYTES - len(kept))]
    return kept.decode(errors="replace") + (f"\n… (cut at {OUTPUT_BYTES:,} bytes)" if cut else "")
