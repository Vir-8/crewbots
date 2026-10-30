"""The daemon as a macOS login service (a launchd LaunchAgent): it starts when you log in, restarts if it crashes, and
keeps running when the app is closed. The app attaches to it and never owns it.

`crewbots service install` writes ~/Library/LaunchAgents/<label>.plist and loads it; `uninstall` unloads and removes
it; `status` says whether it's installed and running. `stop` and `load` take it down and bring it back as installed,
for starting a crew over (reset.py).
"""

import os
import plistlib
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from crewbots import paths

LABEL = "app.crewbots.daemon"


@dataclass(frozen=True)
class ServiceStatus:
    label: str
    installed: bool
    running: bool
    pid: int | None
    plist: Path


def plist_path(label: str = LABEL) -> Path:
    return paths.launch_agents() / f"{label}.plist"


def login_path() -> str:
    """PATH as a login shell has it. launchd starts services with a bare one, and the daemon starts uv, claude and
    codex, which live in Homebrew and ~/.local/bin."""
    home = str(Path.home())
    dirs = os.environ.get("PATH", "").split(":")
    for extra in (f"{home}/.local/bin", f"{home}/.cargo/bin", "/opt/homebrew/bin", "/usr/local/bin",
                  "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if extra not in dirs:
            dirs.append(extra)
    return ":".join(d for d in dirs if d)


def plist(home: Path, port: int, label: str = LABEL, executable: Path | None = None) -> dict:
    """The LaunchAgent: run `crewbots serve` for this crew at login, and again if it ever exits with an error."""
    executable = executable or Path(sys.executable).with_name("crewbots")
    paths.logs().mkdir(parents=True, exist_ok=True)
    return {
        "Label": label,
        "ProgramArguments": [str(executable), "--home", str(home.expanduser()), "serve", "--port", str(port)],
        "EnvironmentVariables": {"PATH": login_path(), "CREWBOTS_SERVICE": label},
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # restart after a crash, not after a clean stop
        "ThrottleInterval": 10,
        "ProcessType": "Interactive",  # it runs agents for the user; don't throttle it like a background job
        "StandardOutPath": str(paths.daemon_log()),
        "StandardErrorPath": str(paths.daemon_log()),
        "WorkingDirectory": str(home.expanduser()),
    }


def install(home: Path, port: int, label: str = LABEL, launchctl: Sequence[str] = ("launchctl",)) -> ServiceStatus:
    path = plist_path(label)
    if path.exists():
        _launchctl(launchctl, "bootout", f"{_domain()}/{label}", check=False)  # reinstall: replace what's loaded
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(plist(home, port, label)))
    _launchctl(launchctl, "bootstrap", _domain(), str(path))
    return status(label, launchctl)


def uninstall(label: str = LABEL, launchctl: Sequence[str] = ("launchctl",)) -> ServiceStatus:
    _launchctl(launchctl, "bootout", f"{_domain()}/{label}", check=False)  # SIGTERM: the daemon shuts down cleanly
    plist_path(label).unlink(missing_ok=True)
    return status(label, launchctl)


def stop(label: str = LABEL, launchctl: Sequence[str] = ("launchctl",)) -> None:
    """Unloads the service, keeping it installed: SIGTERM, so the daemon shuts down cleanly, and nothing starts it
    again until `load`."""
    _launchctl(launchctl, "bootout", f"{_domain()}/{label}", check=False)


def load(label: str = LABEL, launchctl: Sequence[str] = ("launchctl",)) -> None:
    """Loads the installed service again, as it was installed (it starts at once), rather than writing it anew."""
    _launchctl(launchctl, "bootstrap", _domain(), str(plist_path(label)))


def start(label: str = LABEL, launchctl: Sequence[str] = ("launchctl",)) -> None:
    """Starts the installed service if it isn't running (what the app does when it finds no daemon)."""
    _launchctl(launchctl, "kickstart", f"{_domain()}/{label}")


def status(label: str = LABEL, launchctl: Sequence[str] = ("launchctl",)) -> ServiceStatus:
    path = plist_path(label)
    result = _launchctl(launchctl, "print", f"{_domain()}/{label}", check=False)
    pid = None
    running = False
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid = "):
                pid = int(line.removeprefix("pid = "))
            if line == "state = running":
                running = True
    return ServiceStatus(label=label, installed=path.exists(), running=running, pid=pid, plist=path)


LOG_MAX_BYTES = 10_000_000
LOG_KEEP = 3  # daemon.log.1 to .3


def rotate_log(max_bytes: int = LOG_MAX_BYTES, keep: int = LOG_KEEP, log: Path | None = None) -> bool:
    """Starts a fresh daemon.log once it has grown past `max_bytes`, keeping the last `keep`. launchd opened the file
    and holds it as the daemon's output, so the daemon moves its own output: it renames the file and points its stdout
    and stderr at a new one. Only when its output is that file (not a terminal, not the app's pipe)."""
    log = log or paths.daemon_log()
    try:
        current = os.stat(log)
        if current.st_size <= max_bytes or (os.fstat(1).st_ino, os.fstat(1).st_dev) != (current.st_ino, current.st_dev):
            return False
    except OSError:
        return False
    for n in range(keep - 1, 0, -1):
        older = log.with_name(f"{log.name}.{n}")
        if older.exists():
            older.replace(log.with_name(f"{log.name}.{n + 1}"))
    log.replace(log.with_name(f"{log.name}.1"))
    sys.stdout.flush()
    sys.stderr.flush()
    fresh = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fresh, 1)
    os.dup2(fresh, 2)
    os.close(fresh)
    return True


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(launchctl: Sequence[str], *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([*launchctl, *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"launchctl {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result
