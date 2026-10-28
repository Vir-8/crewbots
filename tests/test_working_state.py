from concurrent.futures import ThreadPoolExecutor

import pytest

from crewbots.memory import MemoryError
from crewbots.working_state import LIMIT, edit, read


def test_concurrent_edits_and_entry_operations(tmp_path):
    path = tmp_path / "state.md"
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda text: edit(path, "User's rules", "add", text)[0], ["first", "second"]))
    assert "first" in read(path) and "second" in read(path)
    edit(path, "User's rules", "replace", "changed", ids[0])
    edit(path, "User's rules", "remove", entry_id=ids[1])
    assert "changed" in read(path) and "second" not in read(path)


def test_cap_prunes_done_and_rejects_oversize_active_entries(tmp_path):
    path = tmp_path / "state.md"
    edit(path, "Commitments", "add", "old " * 400, done=True)
    _, pruned = edit(path, "Decisions", "add", "new " * 700)
    assert pruned and "old old" not in read(path) and len(read(path)) <= LIMIT
    with pytest.raises(MemoryError, match="tidy"):
        edit(path, "User's rules", "add", "x" * LIMIT)
    assert len(read(path)) <= LIMIT


def test_memory_history_does_not_track_state_lock(tmp_path):
    import subprocess

    from crewbots.memory import Memory

    memory = Memory(tmp_path)
    edit(memory.root / "worker" / "working-state" / "state.md", "User's rules", "add", "Keep the chats separate")
    (memory.root / "worker" / "working-state" / ".state-abandoned").write_text("private remnant")
    assert memory.commit("Keep working state")
    files = subprocess.run(["git", "-C", str(memory.root), "ls-files"], check=True, capture_output=True, text=True).stdout
    assert "worker/working-state/state.md" in files and ".state.lock" not in files and ".state-abandoned" not in files


def test_generated_overflow_preserves_cap_and_tells_bot_to_tidy(tmp_path):
    from crewbots.working_state import sync

    path = tmp_path / "state.md"
    edit(path, "User's rules", "add", "x" * 3871)
    assert len(read(path)) == LIMIT
    sync(path, [f"- t_{i}: question" for i in range(100)], ["- chat t_chat"])
    assert len(read(path)) > LIMIT
    assert "t_99: question" in read(path) and "chat t_chat" in read(path)
    assert "tidy up your state" in read(path)
    sync(path, [], [])
    assert "tidy up your state" in read(path)


def test_many_concurrent_edits_and_syncs_preserve_every_entry(tmp_path):
    from crewbots.working_state import sync

    path = tmp_path / "state.md"
    workers, per_worker = 6, 8

    def worker(number):
        for index in range(per_worker):
            edit(path, "User's rules", "add", f"entry-{number}-{index}")
            sync(path, [f"- t_{number}: open"], [f"- chat t_{number}"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(worker, range(workers)))
    lines = read(path).splitlines()
    for number in range(workers):
        for index in range(per_worker):
            assert sum(line.endswith(f" entry-{number}-{index}") for line in lines) == 1


def test_generated_flood_keeps_newest_questions_and_does_not_block_a_rule(tmp_path):
    from crewbots.working_state import QUESTION_LIMIT, sync

    path = tmp_path / "state.md"
    sync(path, [f"- task-{i:03}: choose the option for this task" for i in range(100)],
         [f"- chat {i}" for i in range(30)])
    edit(path, "User's rules", "add", "Ask before merging")
    page = read(path)
    questions = page.split("## Open questions", 1)[1].split("## Elsewhere", 1)[0].strip()
    assert len(questions) <= QUESTION_LIMIT and "task-099" in questions and "task-000" not in questions
    assert "Ask before merging" in page and len(page) <= LIMIT
    assert len(page.split("## Elsewhere", 1)[1].splitlines()) <= 13


def test_needs_me_is_capped_and_done_removes_entry(tmp_path):
    path = tmp_path / "state.md"
    ids = [edit(path, "Needs me", "add", f"item {i}")[0] for i in range(13)]
    assert "item 0\n" not in read(path) and "item 12" in read(path)
    edit(path, "Needs me", "replace", "handled", ids[-1], done=True)
    assert "item 12" not in read(path) and "handled" not in read(path)


def test_contended_lock_times_out_and_can_be_retried(tmp_path):
    import fcntl
    import time

    path = tmp_path / "state.md"
    lock_path = tmp_path / ".state.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        start = time.monotonic()
        with pytest.raises(MemoryError, match="busy"):
            edit(path, "User's rules", "add", "Retry me")
        assert time.monotonic() - start < 1
    edit(path, "User's rules", "add", "Retry me")
    assert "Retry me" in read(path)


def test_automatic_decision_budget_reclaims_space_for_user_rule(tmp_path):
    from crewbots.working_state import AUTO_CHARS

    path = tmp_path / "state.md"
    for i in range(12):
        edit(path, "Decisions", "add", f"question-{i:02d}: " + "q" * 145, automatic=True)
    edit(path, "User's rules", "add", "r" * 90)
    state = read(path)
    decisions = state.split("## Decisions", 1)[1].split("## Commitments", 1)[0].strip()
    assert len(decisions) <= AUTO_CHARS and "question-11" in decisions and "question-00" not in decisions
    assert "r" * 90 in state


def test_replacing_automatic_decision_makes_it_owned(tmp_path):
    path = tmp_path / "state.md"
    automatic_id, _ = edit(path, "Decisions", "add", "original", automatic=True)
    owned_id, _ = edit(path, "Decisions", "replace", "distilled decision", automatic_id)
    assert owned_id != automatic_id
    for i in range(12):
        edit(path, "Decisions", "add", f"automatic answer {i}", automatic=True)
    assert f"`{owned_id}` distilled decision" in read(path)
    edit(path, "Decisions", "replace", "updated decision", owned_id)
    assert "updated decision" in read(path)


def test_automatic_decisions_prune_by_timestamp_not_text_order(tmp_path, monkeypatch):
    path = tmp_path / "state.md"
    timestamps = iter([300, 100, 200])
    monkeypatch.setattr("crewbots.working_state.time.time_ns", lambda: next(timestamps))
    edit(path, "Decisions", "add", "alpha " + "x" * 480, automatic=True)
    edit(path, "Decisions", "add", "zulu " + "x" * 480, automatic=True)
    edit(path, "Decisions", "add", "middle " + "x" * 480, automatic=True)
    state = read(path)
    assert "alpha" in state and "middle" in state and "zulu" not in state


def test_stale_temporary_files_are_removed_under_the_lock(tmp_path):
    folder = tmp_path / "worker" / "working-state"
    path = folder / "state.md"
    edit(path, "User's rules", "add", "Ask first")
    stale = folder / ".state-abandoned"
    stale.write_text("private remnant")
    edit(path, "User's rules", "add", "Keep chats separate")
    assert not stale.exists()


def test_done_entries_prune_by_stored_timestamp_not_text(tmp_path, monkeypatch):
    path = tmp_path / "state.md"
    timestamps = iter([300, 100, 200, 400])
    monkeypatch.setattr("crewbots.working_state.time.time_ns", lambda: next(timestamps))
    edit(path, "Decisions", "add", "alpha " + "x" * 720, done=True)
    edit(path, "Commitments", "add", "zulu " + "x" * 720, done=True)
    edit(path, "Decisions", "add", "middle " + "x" * 720, done=True)
    edit(path, "User's rules", "add", "r" * 1800)
    state = read(path)
    assert "alpha" in state and "middle" in state and "zulu" not in state
