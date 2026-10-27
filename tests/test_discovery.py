"""Getting a crew started from what Claude Code and Codex know: the folders worked in lately, and their memories."""

import json
import os
import time

from fakes import git_repo

from crewbots import discovery, paths


def claude_session(folder, cwd, memories=()):
    """A Claude Code project folder, as Claude Code keeps it: a session file whose lines carry the folder."""
    project = paths.home() / ".claude" / "projects" / str(cwd).replace("/", "-")
    project.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "queue-operation"}, {"type": "user", "cwd": str(cwd), "message": {"content": "private words"}}]
    (project / f"{folder}.jsonl").write_text("\n".join(json.dumps(line) for line in lines))
    if memories:
        (project / "memory").mkdir(exist_ok=True)
        for name, text in memories:
            (project / "memory" / name).write_text(text)
    return project


def codex_session(cwd, day="2026/10/27", when=None):
    folder = paths.codex_home() / "sessions" / day
    folder.mkdir(parents=True, exist_ok=True)
    session = folder / f"rollout-{len(list(folder.iterdir()))}.jsonl"
    session.write_text(json.dumps({"type": "session_meta", "payload": {"cwd": str(cwd)}}) + "\n{}\n")
    if when:
        os.utime(session, (when, when))


def test_the_folders_worked_in_lately_with_their_memories(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000)  # late 2027: every file here is recent
    game = git_repo(paths.home() / "Games" / "Sky Islands")
    (game / "README.md").write_text("# Sky Islands\n\n![badge](x.png)\nA cozy [glider](x) game. Made in Unity.\n")
    (game / "Assets").mkdir()
    site = paths.home() / "code" / "site"
    site.mkdir(parents=True)
    claude_session("a", game / "Assets", [("MEMORY.md", "- [Art](art.md)"),
                                           ("art.md", "---\nname: art\ndescription: Stylized \"fantasy\" art\n---\nBody")])
    codex_session(game)
    codex_session(site)
    codex_session(tmp_path)  # a temporary folder: not the user's work
    codex_session(paths.root() / "worktrees" / "x")  # crewbots' own
    codex_session(paths.home() / "Documents" / "Codex" / "2026-10-25" / "scratch")  # Codex's chats without a project

    folders, memories = discovery.found()

    assert {f.folder for f in folders} == {game.resolve(), site.resolve()}
    sky = next(f for f in folders if f.folder == game.resolve())
    assert sky.name == "sky-islands" and sky.sources == {"claude", "codex"} and sky.sessions == 2  # one folder, as a repo
    assert sky.about == "A cozy glider game."
    assert [m.title for m in sky.memories] == ["Its index", 'Stylized "fantasy" art']
    assert memories == []  # Codex keeps no memories here
    text = discovery.digest(sky.memories)
    assert "## Claude Code: ~/.claude/projects/" in text and "Stylized" in text
    assert "private words" not in text  # sessions give their folder, nothing they said


def test_two_folders_of_the_same_name_are_told_apart(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_800_000_000)
    for parent in ("Claude", "Codex"):
        folder = paths.home() / "Documents" / parent / "Trading"
        folder.mkdir(parents=True)
        codex_session(folder)

    folders, _ = discovery.found()

    assert sorted(f.name for f in folders) == ["claude-trading", "codex-trading"]


def test_old_sessions_and_codex_memories(tmp_path, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    old = paths.home() / "old"
    old.mkdir()
    codex_session(old, day="2020/01/01", when=now - 400 * 86_400)
    memories = paths.codex_home() / "memories"
    memories.mkdir(parents=True)
    (memories / "memory_summary.md").write_text("v1\nThe user ships games.")

    folders, found = discovery.found()

    assert folders == [] and [m.path.name for m in found] == ["memory_summary.md"]
    assert "The user ships games." in discovery.digest(found)
