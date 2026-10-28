"""Launch Chrome on inherited CDP fds and clean up only recorded, verified processes.

The exec trampoline remaps fds in a fresh interpreter, never with preexec_fn in the
threaded daemon. It records its identity under the profile lock, then execs Chrome
with the same PID and process start time.
"""

import asyncio
import contextlib
import ctypes
import errno
import fcntl
import json
import logging
import os
import shutil
import signal
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from crewbots.browser_cdp import BrowserError
from crewbots.browser_pipe import Pipe

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OWNER_FILE = "crewbots-owner.json"
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessInfo:
    parent: int
    uid: int
    start_time: str


class LaunchStillRunning(BrowserError):
    """A failed launcher could not be reaped; retain its profile for the next verified sweep."""


class _DarwinBSDInfo(ctypes.Structure):
    # struct proc_bsdinfo from macOS's sys/proc_info.h, including microsecond start time.
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")]
    _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "pjobc", "tdev", "tpgid", "nice")]
    _fields_ += [("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64)]


def process_info(pid: int) -> ProcessInfo | None:
    """Return kernel identity, None only when the process is confirmed gone; otherwise fail closed."""
    if sys.platform == "darwin":
        info = _DarwinBSDInfo()
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        libproc.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        libproc.proc_pidinfo.restype = ctypes.c_int
        ctypes.set_errno(0)
        size = libproc.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))  # PROC_PIDTBSDINFO
        if size == ctypes.sizeof(info) and info.pid == pid:
            return ProcessInfo(info.ppid, info.uid, f"darwin:{info.start_sec}:{info.start_usec}")
        if size == 0 and ctypes.get_errno() == errno.ESRCH:
            return None
        raise BrowserError(f"Cannot verify process identity for PID {pid}")
    if sys.platform.startswith("linux"):
        proc = Path(f"/proc/{pid}")
        try:
            stat = (proc / "stat").read_text()
            uid = proc.stat().st_uid
        except FileNotFoundError as e:
            # A hidden/unmounted procfs is not proof of exit. pidfd_open checks existence
            # without delivering a signal; unsupported or denied checks fail closed.
            try:
                fd = os.pidfd_open(pid)
            except ProcessLookupError:
                return None
            except (OSError, AttributeError) as check:
                raise BrowserError(f"Cannot verify process identity for PID {pid}: {check}") from e
            else:
                os.close(fd)
                raise BrowserError(f"Cannot inspect existing process PID {pid}") from e
        except OSError as e:
            raise BrowserError(f"Cannot verify process identity for PID {pid}: {e}") from e
        try:
            fields = stat[stat.rfind(")") + 2:].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            return ProcessInfo(int(fields[1]), uid, f"linux:{boot}:{fields[19]}")
        except (OSError, ValueError, IndexError) as e:
            raise BrowserError(f"Cannot verify process identity for PID {pid}: {e}") from e
    raise BrowserError(f"Cannot verify browser process identity on {sys.platform}")


def record_owner(profile: Path, pid: int):
    info = process_info(pid)
    if info is None or info.uid != os.getuid():
        raise BrowserError(f"Cannot record browser owner PID {pid}")
    fd = os.open(profile / OWNER_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"pid": pid, "start_time": info.start_time}, stream)
        stream.flush()
        os.fsync(stream.fileno())


def read_owner(profile: Path) -> tuple[int, str]:
    fd = os.open(profile / OWNER_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
            raise ValueError("invalid browser owner record file")
        with os.fdopen(fd) as stream:
            fd = -1
            owner = json.load(stream)
    finally:
        if fd >= 0:
            os.close(fd)
    pid, start = owner["pid"], owner["start_time"]
    if type(pid) is not int or not 1 < pid < 2**31 or not isinstance(start, str) or not start:
        raise ValueError("invalid browser owner record")
    return pid, start


async def launch(argv: list[str], log_file, profile: Path | None = None):
    child_read = parent_write = parent_read = child_write = -1
    process = None
    try:
        child_read, parent_write = os.pipe()
        parent_read, child_write = os.pipe()
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "crewbots.browser_process", str(child_read), str(child_write),
            str(os.getpid()), str(profile) if profile else "", *argv,
            pass_fds=(child_read, child_write), stdout=log_file, stderr=log_file)
        os.close(child_read)
        os.close(child_write)
        child_read = child_write = -1
        read_fd, write_fd = parent_read, parent_write
        parent_read = parent_write = -1  # Pipe.open owns and cleans them even if connection setup fails.
        return process, await Pipe.open(read_fd, write_fd)
    except BaseException as error:
        if process and process.returncode is None:
            try:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                async with asyncio.timeout(1):
                    await process.wait()
            except (OSError, TimeoutError) as failure:
                raise LaunchStillRunning(f"Could not stop browser launcher PID {process.pid}: {failure}") from error
        raise
    finally:
        for fd in (child_read, child_write, parent_read, parent_write):
            if fd >= 0:
                os.close(fd)


def exec_browser(read_fd: int, write_fd: int, parent_pid: int, profile: str, argv: list[str]):
    # A daemon that died while spawning must not leave a newly launched, unrecorded Chrome.
    if os.getppid() != parent_pid:
        raise BrowserError("Browser parent exited during launch")
    if profile:
        record_owner(Path(profile), os.getpid())
    if os.getppid() != parent_pid:
        raise BrowserError("Browser parent exited during launch")
    # Duplicate above fd 4 before remapping: either source may itself be fd 3 or 4.
    sources = [fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 5) for fd in (read_fd, write_fd)]
    os.dup2(sources[0], 3, inheritable=True)
    os.dup2(sources[1], 4, inheritable=True)
    for fd in set(sources + [read_fd, write_fd]) - {3, 4}:
        os.close(fd)
    os.execv(argv[0], argv)


class Profiles:
    """An exclusive lifetime lock stops a new daemon from cleaning a live daemon's profiles."""

    def __init__(self, root: Path, *, sweep: bool = True):
        self.root = root.absolute()
        self.fd = None
        self.sweep_enabled = sweep

    async def acquire(self):
        if self.root.is_symlink():
            raise BrowserError("The browser profile root must not be a symlink")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BrowserError("Another browser service owns this profile folder") from None
            if self.sweep_enabled:
                await self.sweep()
        except BaseException:
            self.close()
            raise

    async def sweep(self):
        for profile in self.root.glob("chrome-*"):
            if not profile.is_dir() or profile.is_symlink():
                continue
            try:
                try:
                    pid, started = read_owner(profile)
                except FileNotFoundError:
                    # The launcher records ownership before exec. Old ownerless profiles
                    # can be removed without guessing or signalling a process.
                    if time.time() - profile.stat().st_mtime > 3600:
                        shutil.rmtree(profile)
                    else:
                        log.warning("Leaving unverified browser profile %s: owner file missing", profile)
                    continue
                info = process_info(pid)
                if info is not None:
                    if info.start_time != started or info.uid != os.getuid() or info.parent != 1:
                        log.warning("Leaving browser profile %s: PID %s PPID %s does not match a verified orphan",
                                    profile, pid, info.parent)
                        continue
                    await self._terminate(pid, started)
                    info = process_info(pid)
                    if info is not None:
                        log.warning("Leaving browser profile %s: PID %s PPID %s is still present", profile, pid, info.parent)
                        continue
                if not profile.is_symlink():
                    shutil.rmtree(profile)
            except (OSError, ValueError, KeyError, TypeError, BrowserError) as e:
                # An unrecorded/legacy or unverifiable process must never be guessed from ps arguments.
                log.warning("Leaving unverified browser profile %s: %s", profile, e)

    async def _terminate(self, pid: int, started: str):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            # PID may have been reused since the last observation; verify before each signal.
            info = process_info(pid)
            if info is None or info.start_time != started or info.uid != os.getuid() or info.parent != 1:
                return
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, sig)
            for _ in range(20):
                info = process_info(pid)
                if info is None or info.start_time != started:
                    return
                await asyncio.sleep(0.05)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


if __name__ == "__main__":
    exec_browser(int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5:])
