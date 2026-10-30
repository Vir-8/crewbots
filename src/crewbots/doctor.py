"""`crewbots doctor`: whether everything crewbots stands on is in order, and what's taking room on the disk.

It only looks: each finding says how to fix or clean up, and nothing is changed or removed. The CLIs are asked at the
same time, so it takes about as long as the slowest of them.
"""

import json
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import yaml

from crewbots import crew, paths
from crewbots import service as svc
from crewbots.store import Status

STALE_AFTER = timedelta(days=14)  # a finished task's worktree left this long is probably forgotten
FINISHED = {Status.CANCELLED, Status.FAILED, Status.INTERRUPTED}


@dataclass(frozen=True)
class Finding:
    ok: bool | None  # True: fine; False: something to fix; None: for information
    line: str
    hint: str | None = None  # what to do about it, or how to clean it up


def run(home: Path) -> list[Finding]:
    home = home.expanduser()
    with ThreadPoolExecutor(max_workers=6) as pool:
        asked = [pool.submit(f) for f in (_claude, _codex, lambda: _daemon(home), _service, lambda: _database(home),
                                          lambda: _disk(home))]
        found = [finding for future in asked for finding in future.result()]
    return found + _chief(home) + _worktrees(home) + _log()


def _chief(home: Path) -> list[Finding]:
    db = home / paths.DB
    if not db.exists():
        return []
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            row = conn.execute("select value from crew_settings where key = 'chief'").fetchone()
    except sqlite3.Error:
        return []  # the database check reports errors; old crews migrate on daemon start
    if not row:
        return []
    name = str(row[0]).removeprefix("bot:")
    for path in (home / "bots").glob("*.md"):
        try:
            if crew.parse_card(path.read_text(), path.stem, path).name == name:
                return []
        except (OSError, ValueError, yaml.YAMLError):
            continue
    return [Finding(False, f"Chief: configured bot {name!r} is missing; chief-only actions are disabled",
                    "Restore its role card or choose an existing bot with PUT /v1/crew/chief.")]


def _cli(*argv: str) -> str | None:
    if not shutil.which(argv[0]):
        return None
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (done.stdout or done.stderr).strip()


def _claude() -> list[Finding]:
    version = _cli("claude", "--version")
    if version is None:
        return [Finding(False, "Claude Code isn't installed", "https://code.claude.com, then `claude` to sign in")]
    try:
        auth = json.loads(_cli("claude", "auth", "status") or "{}")
    except ValueError:
        auth = {}
    if not auth.get("loggedIn"):
        return [Finding(False, f"Claude Code {version.split()[0]}: not signed in", "claude auth login")]
    # Only how it's signed in: never the account's email.
    return [Finding(True, f"Claude Code {version.split()[0]}: signed in ({auth.get('authMethod', 'yes')})")]


def _codex() -> list[Finding]:
    version = _cli("codex", "--version")
    if version is None:
        return [Finding(False, "Codex isn't installed", "npm install -g @openai/codex, then `codex login`")]
    status = _cli("codex", "login", "status") or ""
    number = version.split()[-1]
    if not status.lower().startswith("logged in"):
        return [Finding(False, f"Codex {number}: not signed in", "codex login")]
    return [Finding(True, f"Codex {number}: {status[0].lower()}{status[1:]}".replace("logged in", "signed in"))]


def _daemon(home: Path) -> list[Finding]:
    try:
        info = json.loads((home / paths.DAEMON_FILE).read_text())
        url, headers = info["url"], {"Authorization": f"Bearer {info['token']}"}
    except (OSError, ValueError, TypeError, KeyError):
        return [Finding(False, "Daemon: not running", "crewbots serve, or crewbots service install to run it at login")]
    try:
        version = httpx.get(f"{url}/v1/version", headers=headers, timeout=3).raise_for_status().json()
        state = httpx.get(f"{url}/v1/crew", headers=headers, timeout=3).raise_for_status().json()
    except (httpx.HTTPError, ValueError, KeyError):
        return [Finding(False, f"Daemon: pid {info.get('pid')} isn't answering",
                        f"its log: {paths.daemon_log()}; crewbots service start")]
    doing = (f"{state['turns_running']} turn(s) running" if state.get("turns_running") else "idle") + (
        f", {state['held']} held" if state.get("held") else "") + (", paused" if state.get("paused") else "")
    line = f"Daemon: pid {info['pid']}, from {version.get('build') or 'an installed copy'} (API v{version['api']}); {doing}"
    if version.get("source_changed"):
        return [Finding(None, line + "; its checkout has moved on since it started",
                        "to run the new code: crewbots restart --when-idle")]
    return [Finding(True, line)]


def _service() -> list[Finding]:
    state = svc.status()
    if not state.installed:
        return [Finding(None, "Login service: not installed, so the crew stops when the app does",
                        "crewbots service install")]
    return [Finding(state.running, f"Login service: installed, {'running' if state.running else 'not running'}",
                    None if state.running else "crewbots service start")]


def _database(home: Path) -> list[Finding]:
    db = home / paths.DB
    if not db.exists():
        return [Finding(None, "Database: none yet (it's made when the daemon first starts)")]
    size = sum(p.stat().st_size for p in db.parent.glob(f"{paths.DB}*") if p.is_file())
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10) as conn:  # beside the running daemon
            check = conn.execute("pragma quick_check").fetchone()[0]
            tasks = conn.execute("select count(*) from tasks").fetchone()[0]
            events = conn.execute("select count(*) from events").fetchone()[0]
    except sqlite3.Error as e:
        return [Finding(False, f"Database: couldn't be read ({e})", f"back up {db}, then sqlite3 it '.recover'")]
    if check != "ok":
        return [Finding(False, f"Database: damaged ({check})", f"back up {db}, then sqlite3 it '.recover'")]
    return [Finding(True, f"Database: {_size(size)}, intact; {tasks:,} tasks, {events:,} events")]


def _disk(home: Path) -> list[Finding]:
    places = [("worktrees", paths.worktrees(), "each writing task's checkout; merged ones are removed"),
              ("code review clones", paths.review_snapshots() / "repos", "kept for the next review of each repo"),
              ("code review checkouts", paths.review_snapshots() / "checkouts", "six kept per repo"),
              ("attachments", home / "attachments", "files sent with messages"),
              ("temp", paths.temp(), "sessions' scratch folders, removed as they close"),
              ("logs", paths.logs(), f"rotated past {_size(svc.LOG_MAX_BYTES)}")]
    sizes = {name: _du(path) for name, path, _ in places}
    line = ", ".join(f"{name} {_size(sizes[name])}" for name, _, _ in places if sizes[name])
    hints = [f"{name}: {path} ({why})" for name, path, why in places if sizes[name] > 500_000_000]
    return [Finding(None, f"Disk: {line or 'nothing yet'}", "big: " + "; ".join(hints) if hints else None)]


def _worktrees(home: Path) -> list[Finding]:
    """Folders under ~/.crewbots/worktrees that no task needs: empty ones, ones whose task is gone or merged, and ones
    whose task finished long ago."""
    root = paths.worktrees()
    if not root.is_dir():
        return []
    tasks = _tasks(home / paths.DB)
    now = datetime.now(UTC)
    stale: list[tuple[Path, str, str]] = []  # folder, why, how to remove it
    for repo in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "reviews"):
        children = [c for c in repo.iterdir() if not c.name.startswith(".")]
        if not children:
            stale.append((repo, "empty", f"rmdir {repo}"))
            continue
        for wt in sorted(c for c in children if c.is_dir()):
            task = tasks.get(wt.name)
            remove = f"git -C {task['repo']} worktree remove {wt}" if task and task["repo"] else f"rm -r {wt}"
            if task is None:
                stale.append((wt, "its task is gone", remove))
            elif task["merge_state"] == "merged":
                stale.append((wt, "merged", remove))
            elif task["status"] in FINISHED and now - _when(task["updated_at"]) > STALE_AFTER:
                stale.append((wt, f"{task['status']} {(now - _when(task['updated_at'])).days} days ago", remove))
    if not stale:
        return [Finding(True, "Worktrees: none left over")]
    lines = "\n".join(f"{folder} ({why}): {remove}" for folder, why, remove in stale)
    return [Finding(None, f"Worktrees: {len(stale)} left over that no task needs", lines)]


def _log() -> list[Finding]:
    log = paths.daemon_log()
    if not log.exists():
        return []
    rotated = sorted(log.parent.glob(f"{log.name}.*"))
    extra = f", and {len(rotated)} older" if rotated else ""
    return [Finding(None, f"Log: {log} ({_size(log.stat().st_size)}{extra}); a fresh one starts past {_size(svc.LOG_MAX_BYTES)}")]


def _tasks(db: Path) -> dict[str, dict]:
    if not db.exists():
        return {}
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("select id, status, repo, merge_state, updated_at from tasks where worktree_path is not "
                                "null").fetchall()
    except sqlite3.Error:
        return {}
    return {r["id"]: dict(r) for r in rows}


def _when(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _du(path: Path) -> int:
    if not path.exists():
        return 0
    try:  # the system's du is far quicker than walking a big checkout from Python
        done = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, timeout=60)
        return int(done.stdout.split()[0]) * 1024
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return 0


def _size(n: int) -> str:
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("KB", 1e3)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} B"

