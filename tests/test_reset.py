"""Starting a crew over: what was said, and what the bots kept, moves into a backup; the crew's setup and code review
stay, and the chief greets the user again."""

import json
import os
from datetime import datetime

import pytest
from fakes import FakeBackend

from crewbots import crew as crew_module
from crewbots import paths, reset
from crewbots.runner import Runner
from crewbots.runner.welcome import STARTED
from crewbots.store import Store

WHEN = datetime(2026, 10, 27, 14, 5)


def session(path, cwd):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "user", "cwd": str(cwd)}) + "\n")
    return path


def rollout(day, session_id, cwd, originator="crewbots"):
    path = paths.codex_home() / "sessions" / day / f"rollout-2026-10-27T10-00-00-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"id": session_id, "cwd": str(cwd), "originator": originator}
    path.write_text(json.dumps({"type": "session_meta", "payload": meta}) + "\n")
    return path


@pytest.fixture
def home(tmp_path):
    """A crew that's been talked to: a chat and the thread it started, a group chat, a code review with a draft, the
    chief's notes and pages, a project and a shared file, and sessions in Claude Code's and Codex's folders: the
    bots', code review's, and the user's own."""
    home = crew_module.init(tmp_path / "crew")
    store = Store(home / "crewbots.db")
    chat = store.create_task("chief", "Chat with chief", kind="chat")
    store.update_task(chat.id, session_id="chat-now")
    store.append_event(chat.id, kind="message", data={"from": "user", "text": "I make cozy games"})
    store.append_event(chat.id, kind="session_started", data={"session_id": "chat-before"})
    store.create_task("engineer", "Fix the build", parent_id=chat.id)
    review = store.create_task("reviewer:claude:sonnet:high", "Review acme/web#1", kind="review", pr="acme/web#1")
    store.update_task(review.id, session_id="review-session")
    store.append_event(review.id, kind="message", data={"from": "user", "text": "Review acme/web#1"})
    store.append_event(review.id, kind="text", data={"text": "An off-by-one in the pager"})
    store.add_draft("acme/web", 1, "pager.py", 3, "RIGHT", "Off by one", "user", review.id)
    room = store.create_room("Launch", "chief", ["chief", "engineer"])
    store.add_room_message(room["id"], "user", "Ship it?")
    store.set_pins(["bot:chief", f"room:{room['id']}"])
    store.routine_ran("standup", "2026-10-27T09:00:00+05:30", chat.id)
    store.routine_ran(STARTED, "2026-10-27T13:18:37+05:30", "")
    store.set_wake("chief", looked_at="2026-10-27T07:36:37+00:00")
    store.close()

    (home / "memory" / "chief" / "notes").mkdir(parents=True)
    (home / "memory" / "chief" / "notes" / "who.md").write_text("Makes cozy games.\n")
    (home / "projects").mkdir()
    (home / "projects" / "sky-islands.md").write_text("---\nfolder: ~/Games/Sky Islands\n---\n")
    (home / "attachments" / "abc").mkdir(parents=True)
    (home / "attachments" / "abc" / "shot.png").write_bytes(b"png")
    pages = paths.bot_folders() / "chief"
    pages.mkdir(parents=True)
    (pages / "Scratchpad.md").write_text("# Top of mind\n")

    claude = paths.home() / ".claude" / "projects"
    session(claude / "-crew" / "chat-old.jsonl", home)  # the bots' chats ran in the crew's folder once
    session(claude / "-chief" / "chat-now.jsonl", pages)
    session(claude / "-worktree" / "chat-before.jsonl", tmp_path / "worktree")
    (claude / "-worktree" / "chat-before").mkdir()
    session(claude / "-review" / "review-session.jsonl", tmp_path / "checkout")
    session(claude / "-game" / "mine.jsonl", tmp_path / "game")  # the user's own
    (claude / ".DS_Store").write_bytes(b"")  # Finder's, beside the folders
    rollout("2026/10/27", "engineer-chat", paths.bot_folders() / "engineer")
    rollout("2026/10/27", "review-codex", tmp_path / "checkout")
    rollout("2026/10/27", "users-own", paths.bot_folders() / "engineer", originator="codex_cli_rs")
    return home


def test_it_moves_the_conversations_and_what_the_bots_kept_into_a_backup(home):
    done = reset.reset(home, WHEN)

    backup = paths.root() / "backups" / "2026-10-27-140500"
    assert done.backup == backup and done.tasks == 2
    assert (backup / "memory" / "chief" / "notes" / "who.md").read_text() == "Makes cozy games.\n"
    assert (backup / "projects" / "sky-islands.md").is_file() and (backup / "attachments" / "abc" / "shot.png").is_file()
    assert (backup / "folders" / "chief" / "Scratchpad.md").is_file()
    assert not (home / "memory").exists() and not (home / "projects").exists()
    assert not (paths.bot_folders() / "chief").exists()

    old = Store(backup / "crewbots.db")  # the database as it was
    assert len(old.list_tasks()) == 3
    old.close()


def test_the_bots_sessions_go_and_everyone_elses_stay(home):
    reset.reset(home, WHEN)

    claude = paths.home() / ".claude" / "projects"
    saved = paths.root() / "backups" / "2026-10-27-140500" / "sessions"
    assert not (claude / "-crew").exists() and (saved / "claude" / "-crew" / "chat-old.jsonl").is_file()
    assert not (claude / "-chief").exists()
    assert sorted(p.name for p in (saved / "claude" / "-worktree").iterdir()) == ["chat-before", "chat-before.jsonl"]
    assert (claude / "-review" / "review-session.jsonl").is_file()  # code review's
    assert (claude / "-game" / "mine.jsonl").is_file()  # the user's own
    left = sorted(p.name for p in (paths.codex_home() / "sessions" / "2026/10/27").iterdir())
    assert left == [f"rollout-2026-10-27T10-00-00-{s}.jsonl" for s in ("review-codex", "users-own")]
    assert [p.name for p in (saved / "codex" / "2026/10/27").iterdir()] == ["rollout-2026-10-27T10-00-00-engineer-chat.jsonl"]


def test_the_setup_and_code_review_stay(home):
    reset.reset(home, WHEN)

    store = Store(home / "crewbots.db")
    [review] = store.list_tasks()
    assert review.kind == "review" and [e.kind for e in store.events(review.id)] == ["message", "text"]
    assert len(store.drafts("acme/web", 1)) == 1
    assert store.rooms() == [] and store.pins() == ["bot:chief"]
    assert store.routine_state(STARTED) is None
    assert store.routine_state("standup")["last_task_id"] is None  # when it last ran stays, so it doesn't run again
    assert store.wake_state("chief")["looked_at"] is None
    assert [r["task_id"] for r in store.search("cozy")] == []
    store.close()
    assert crew_module.load(home).bots.keys() == {"chief", "engineer", "reviewer", "critic"}


def test_then_the_chief_greets_the_user_again(home):
    reset.reset(home, WHEN)

    crew = crew_module.load(home)
    store = Store(home / "crewbots.db")
    runner = Runner(store, crew, {"fake": FakeBackend()})
    chat = runner.greet()
    assert chat is not None and store.events(chat.id)[0].data["text"].startswith("Hey! I'm your Chief of Staff.")
    store.close()


def test_not_while_the_daemon_runs(home):
    (home / "daemon.json").write_text(json.dumps({"url": "http://127.0.0.1:8765", "token": "t", "pid": os.getpid()}))

    with pytest.raises(reset.ResetError, match="daemon is running"):
        reset.reset(home, WHEN)
    assert (home / "memory").is_dir() and not (paths.root() / "backups").exists()
