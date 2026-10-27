"""Starting a crew over: its conversations and what its bots kept move into a backup (~/.crewbots/backups/<when>/),
and its setup stays as it is, so the chief greets the user again as it did the first time.

Moved: every chat, thread and group chat with what was said in them, and the bots' sessions of them (their Claude
Code and Codex session files); the bots' memory, and their folders in Documents, pages and all; the projects; and the
files shared in chats. Kept: the bots and their role cards, the team's files, the routines, the skills, the MCP
servers, and code review, which keeps its own reviews, drafts and sessions.

Nothing is deleted: the backup has the database as it was, and every file moved, by where it came from. The daemon
mustn't be running, since it holds the database and the sessions.
"""

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from crewbots import crew as crew_module
from crewbots import paths
from crewbots.discovery import _claude_cwd
from crewbots.runner.welcome import STARTED
from crewbots.store import Store

DB = "crewbots.db"
DAEMON_FILE = "daemon.json"
KEPT = "kind = 'review'"  # code review's own tasks, which stay with their events
FOLDERS = ("memory", "projects", "attachments")  # in the crew's folder
# Tables of the crew's conversations, and of what was brought in for it, cleared whole. The rest stay: code review's
# drafts, the routines' state, what triggers have started on, the digests sent and what the crew noticed around it, so
# what ran once doesn't again.
CLEARED = ("room_messages", "rooms", "handoffs", "pending_turns", "primed", "agent_log", "recall_log", "intents",
           "proactive", "reads", "wakes", "brought_in", "permissions")


class ResetError(RuntimeError):
    pass


@dataclass
class Reset:
    backup: Path
    tasks: int = 0  # chats, threads and group chats' seats cleared
    moved: list[Path] = field(default_factory=list)  # where each file or folder moved was


def reset(home: Path, when: datetime | None = None) -> Reset:
    """Starts the crew in `home` over, moving what it clears into a backup. Not while its daemon runs."""
    home = home.expanduser()
    crew = crew_module.load(home)
    if pid := _daemon_pid(home):
        raise ResetError(f"the crew's daemon is running (pid {pid}): quit the app and stop the daemon first")
    done = Reset(paths.root() / "backups" / f"{when or datetime.now():%Y-%m-%d-%H%M%S}")
    done.backup.mkdir(parents=True)
    store = Store(home / DB)  # migrated, so every table below is there
    try:
        store.db.execute("vacuum into ?", (str(done.backup / DB),))
        sessions = _clear(store, done)
    finally:
        store.close()
    own = [home, *(crew.pages_of(b) for b in crew.bots.values())]  # where the bots' chats have run
    for name in FOLDERS:
        _move(home / name, done.backup / name, done)
    for bot in crew.bots.values():
        _move(crew.pages_of(bot), done.backup / "folders" / bot.name, done)
    claude = paths.home() / ".claude" / "projects"
    for path in _claude_sessions(claude, sessions, own):
        _move(path, done.backup / "sessions" / "claude" / path.relative_to(claude), done)
    codex = paths.codex_home() / "sessions"
    for path in _codex_sessions(codex, sessions, own):
        _move(path, done.backup / "sessions" / "codex" / path.relative_to(codex), done)
    return done


def _clear(store: Store, done: Reset) -> set[str]:
    """Clears the crew's conversations from the database, but for code review's, and gives their sessions' ids."""
    db = store.db
    gone = f"select id from tasks where not ({KEPT})"
    sessions = {r[0] for r in db.execute(f"select session_id from tasks where not ({KEPT})").fetchall()}
    sessions |= {json.loads(r[0]).get("session_id") for r in db.execute(
        f"select data from events where kind = 'session_started' and task_id in ({gone})").fetchall()}
    done.tasks = db.execute(f"select count(*) from ({gone})").fetchone()[0]
    db.execute("begin")
    try:
        db.execute(f"delete from reviews where task_id in ({gone}) or work_id in ({gone})")
        db.execute(f"delete from events where task_id in ({gone})")
        for table in CLEARED:
            db.execute(f"delete from {table}")
        db.execute("delete from pins where key not like 'bot:%'")  # a group chat's: it's gone
        db.execute("delete from routines where name = ?", (STARTED,))
        db.execute("update routines set last_task_id = null")
        db.execute(f"delete from tasks where not ({KEPT})")
        for table in ("search", "trigrams"):
            db.execute(f"delete from {table} where task_id not in (select id from tasks)")
        db.execute("commit")
    except BaseException:
        db.execute("rollback")
        raise
    db.execute("vacuum")  # what was cleared doesn't linger in the file's free pages
    return {s for s in sessions if s}


def _claude_sessions(root: Path, ids: set[str], own: list[Path]) -> list[Path]:
    """Claude Code's files of the bots' sessions: a folder's whole, for one of crewbots' own folders, and elsewhere
    each session by its id (its transcript, and the folder beside it for what its subagents and tools kept)."""
    found = []
    for folder in sorted(f for f in root.iterdir() if f.is_dir()) if root.is_dir() else []:
        cwd = next((c for c in map(_claude_cwd, folder.glob("*.jsonl")) if c), None)
        if cwd and _within(cwd, own):
            found.append(folder)
        else:
            found += [p for p in sorted(folder.iterdir()) if p.name.removesuffix(".jsonl") in ids]
    return found


def _codex_sessions(root: Path, ids: set[str], own: list[Path]) -> list[Path]:
    """Codex's files of the bots' sessions, one a session in <year>/<month>/<day>/: the ones crewbots started (their
    first line says so, with the session's id and folder) that were a cleared task's or ran in crewbots' own folders."""
    found = []
    for session in sorted(root.glob("*/*/*/rollout-*.jsonl")) if root.is_dir() else []:
        try:
            with session.open(errors="replace") as lines:
                meta = json.loads(lines.readline()).get("payload") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if meta.get("originator") == "crewbots" and (
                meta.get("id") in ids or (meta.get("cwd") and _within(Path(meta["cwd"]), own))):
            found.append(session)
    return found


def _within(path: Path, folders: list[Path]) -> bool:
    path = path.expanduser().resolve()
    return any(path.is_relative_to(f.expanduser().resolve()) for f in folders)


def _move(path: Path, to: Path, done: Reset) -> None:
    if not path.exists():
        return
    to.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(path, to)
    done.moved.append(path)


def _daemon_pid(home: Path) -> int | None:
    """The crew's daemon's process, while it runs."""
    try:
        pid = int(json.loads((home / DAEMON_FILE).read_text())["pid"])
        os.kill(pid, 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return pid
