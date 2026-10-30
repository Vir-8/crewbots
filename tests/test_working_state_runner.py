"""The working state the runner keeps for each bot: decisions from the user's answers, open questions and permissions,
what it has under way elsewhere, and turns that carry on when the state can't be written."""

import pytest
from fakes import bot, make, settled, wait_until

from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


async def test_user_answer_to_escalation_is_shared_as_decision(store, tmp_path):
    runner, backend = make(store, tmp_path)
    task = runner.chat("worker")
    runner.escalate(task.id, "Which database?")
    runner.send(task.id, "SQLite", sender="user")
    await wait_until(settled(store, runner, task.id))
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    assert "Which database?" in state and "answered in chat" in state and "SQLite" not in state
    runner.send(task.id, "Another message", sender="user")
    await wait_until(lambda: len(backend.opened[0].turns) == 2 and settled(store, runner, task.id)())
    after = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    assert after.count("Which database?") == state.count("Which database?") == 1


async def test_open_user_questions_and_permissions_follow_answers(store, tmp_path):
    runner, _ = make(store, tmp_path)
    store.set_chief("worker")  # the one who asks the user's leave
    chat = runner.chat("worker")
    thread = store.create_task("worker", "Export", parent_id=chat.id, kind="thread", status=Status.IDLE)
    runner.escalate(chat.id, "Which export format?")
    permission = runner.ask_permission(thread.id, "Read the extra folder", kind="action")
    runner._sync_working_state("worker")
    path = runner.crew.memory.root / "worker" / "working-state" / "state.md"
    state = path.read_text()
    assert "Which export format?" in state and "Read the extra folder" in state
    assert "question open" in state

    runner.send(chat.id, "CSV", sender="user")
    runner.answer_permission(permission["id"], True)
    await wait_until(lambda: not runner.running())
    runner._sync_working_state("worker")
    state = path.read_text()
    questions = state.split("## Open questions", 1)[1].split("## Elsewhere", 1)[0]
    assert "Which export format?" not in questions and "Read the extra folder" not in questions
    assert "Which export format?" in state and "answered in chat" in state and "CSV" not in state


async def test_elsewhere_has_metadata_without_conversation_text(store, tmp_path):
    runner, _ = make(store, tmp_path, None, bot("worker"), bot("helper"))
    room = runner.create_room("Engineering", ["worker", "helper"])
    chat = runner.chat("worker")
    secret = "unrelated private conversation sentinel"
    runner.post(room["id"], "user", f"@worker {secret}")
    await wait_until(lambda: not runner.running())
    runner._sync_working_state("worker")
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    elsewhere = state.split("## Elsewhere", 1)[1]
    assert chat.id in elsewhere and room["id"] in elsewhere
    assert secret not in state and len([line for line in elsewhere.splitlines() if line.startswith("- ")]) <= 12


def test_elsewhere_says_a_thread_is_the_bots_own_and_who_it_is_for(store, tmp_path):
    runner, _ = make(store, tmp_path, None, bot("worker"), bot("lead"))
    room = runner.create_room("Engineering", ["worker", "lead"], lead="lead")
    seat = store.create_task("lead", "Engineering (lead)", kind="seat", room_id=room["id"], status=Status.IDLE)
    handed = store.create_task("worker", "Add a force-push option", parent_id=seat.id, created_by="lead",
                               status=Status.RUNNING)
    merged = store.create_task("worker", "Old fix", parent_id=seat.id, created_by="lead", status=Status.IDLE)
    store.update_task(merged.id, merge_state="merged")
    own = store.create_task("worker", "Look into the logs", parent_id=runner.chat("worker").id, created_by="worker",
                            status=Status.IDLE)
    runner._sync_working_state("worker")
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    lines = [line for line in state.split("## Elsewhere", 1)[1].splitlines() if line.startswith("- ")]
    assert lines[0].startswith(f"- your thread {handed.id} for lead in 'Engineering' · running · ")
    assert lines[0].endswith(" · Add a force-push option")
    assert lines[1].startswith(f"- your thread {own.id} · idle · ") and lines[1].endswith(" · Look into the logs")
    assert merged.id not in state  # closed work isn't listed
    assert lines[2].startswith("- your chat with the user (")


async def test_long_answer_links_to_full_answer_and_full_state_does_not_block_user(store, tmp_path):
    from crewbots.working_state import edit

    runner, _ = make(store, tmp_path)
    chat = runner.chat("worker")
    runner.escalate(chat.id, "Choose the migration plan")
    answer = "Use the incremental plan. " + "detail " * 60
    runner.send(chat.id, answer)
    await wait_until(lambda: not runner.running())
    path = runner.crew.memory.root / "worker" / "working-state" / "state.md"
    state = path.read_text()
    assert "Use the incremental plan" not in state and f"(task {chat.id})" in state
    assert answer not in state
    edit(path, "User's rules", "add", "x" * (3800 - len(state)))
    runner.escalate(chat.id, "Another decision?")
    runner.send(chat.id, "Proceed")
    await wait_until(lambda: not runner.running())
    assert any(e.data.get("text") == "Proceed" for e in store.events(chat.id) if e.kind == "message")


async def test_choices_and_automatic_decisions_past_the_limit_keep_only_newest(store, tmp_path):
    runner, _ = make(store, tmp_path)
    chat = runner.chat("worker")
    for i in range(13):  # one past the limit of 12
        runner.escalate(chat.id, f"question-{i:02d}?", [{"label": "SQLite"}, {"label": "Postgres"}])
        runner.send(chat.id, "SQLite")
        await wait_until(settled(store, runner, chat.id))
    text = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    decisions = text.split("## Decisions", 1)[1].split("## Commitments", 1)[0]
    assert decisions.count("SQLite") == 12
    assert "question-12?" in decisions and "question-01?" in decisions and "question-00?" not in decisions


@pytest.mark.parametrize("where", ["write", "sync", "read"])
async def test_state_sync_error_does_not_stop_a_turn(store, tmp_path, monkeypatch, caplog, where):
    runner, backend = make(store, tmp_path)

    def broken(*args, **kwargs):
        raise OSError(28, "disk full")

    if where == "write":
        monkeypatch.setattr("crewbots.working_state.sync", broken)
    elif where == "sync":
        monkeypatch.setattr(runner, "_sync_working_state", broken)
    else:
        monkeypatch.setattr("crewbots.working_state.read", broken)
    task = runner.say("worker", "Continue despite disk error")
    await wait_until(settled(store, runner, task.id))
    assert store.get_task(task.id).status == Status.IDLE
    assert backend.opened[0].turns == ["Continue despite disk error"]
    assert "Could not sync working state" in caplog.text


@pytest.mark.parametrize("terminal", [Status.FAILED, Status.CANCELLED])
def test_only_user_answers_open_question_and_terminal_task_drops_it(store, tmp_path, terminal):
    runner, _ = make(store, tmp_path)
    chat = runner.chat("worker")
    runner.escalate(chat.id, "User decision?")
    store.update_task(chat.id, status=Status.NEEDS_INPUT)
    runner._escalated.discard(chat.id)
    store.append_event(chat.id, kind="message", data={"from": "helper", "text": "A teammate replied"})
    runner._sync_working_state("worker")
    path = runner.crew.memory.root / "worker" / "working-state" / "state.md"
    assert "User decision?" in path.read_text()
    runner._set(chat.id, status=terminal)
    assert "User decision?" not in path.read_text()
    store.update_task(chat.id, status=Status.QUEUED)
    runner._sync_working_state("worker")
    assert "User decision?" not in path.read_text()


def test_open_questions_keep_the_newest_within_their_limit(store, tmp_path):
    from crewbots.working_state import QUESTION_LIMIT

    runner, _ = make(store, tmp_path)
    for i in range(8):  # each line is about 170 characters, so not all of them fit
        task = store.create_task("worker", f"work {i}", status=Status.NEEDS_INPUT)
        store.append_event(task.id, kind="escalation",
                           data={"question": f"Question number {i}? " + "Which way should the export go? " * 5})
    assert runner._sync_working_state("worker")
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    questions = state.split("## Open questions", 1)[1].split("## Elsewhere", 1)[0].strip()
    assert len(questions) <= QUESTION_LIMIT
    assert "Question number 7?" in questions and "Question number 0?" not in questions


def test_failed_sync_does_not_prime_or_deliver_changed_state(store, tmp_path, monkeypatch, caplog):
    from crewbots.working_state import edit

    runner, _ = make(store, tmp_path)
    chat = runner.chat("worker")
    store.update_task(chat.id, session_id="existing")
    chat = store.get_task(chat.id)
    _, primed = runner._with_updates(chat, runner.crew.bots["worker"], "first")
    store.set_primed(chat.id, primed)
    edit(runner.crew.memory.root / "worker" / "working-state" / "state.md", "User's rules", "add", "new shared rule")

    def broken(_bot):
        raise OSError(28, "disk full")

    monkeypatch.setattr(runner, "_sync_working_state", broken)
    prompt, seen = runner._with_updates(chat, runner.crew.bots["worker"], "next")
    assert "new shared rule" not in prompt
    assert seen is None or seen["working-state"] == primed["working-state"]
    assert "Could not sync working state" in caplog.text


async def test_room_seat_records_multiple_selected_choices_only(store, tmp_path):
    runner, _ = make(store, tmp_path, None, bot("worker"), bot("helper"))
    room = runner.create_room("Engineering", ["worker", "helper"])
    seat = store.create_task("worker", "seat", kind="seat", room_id=room["id"], status=Status.IDLE)
    runner.escalate(seat.id, "Which databases?", [{"label": "SQLite"}, {"label": "Postgres"}], multiple=True)
    runner.send(seat.id, "- SQLite\n- Postgres", sender="user",
                question_id=store.last_event(seat.id, ("escalation",)).id)
    await wait_until(settled(store, runner, seat.id))
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    assert "Which databases?" in state and "SQLite, Postgres" in state


@pytest.mark.parametrize("terminal", [Status.FAILED, Status.CANCELLED])
def test_unanswered_permission_is_removed_when_task_ends(store, tmp_path, terminal):
    runner, _ = make(store, tmp_path)
    store.set_chief("worker")  # the one who asks the user's leave
    chat = runner.chat("worker")
    runner.ask_permission(chat.id, "Approve the action", kind="action")
    store.update_task(chat.id, status=Status.NEEDS_INPUT)
    runner._escalated.discard(chat.id)
    runner._sync_working_state("worker")
    path = runner.crew.memory.root / "worker" / "working-state" / "state.md"
    assert "Approve the action" in path.read_text()
    runner._set(chat.id, status=terminal)
    assert "Approve the action" not in path.read_text()
    store.update_task(chat.id, status=Status.QUEUED)
    runner._sync_working_state("worker")
    assert "Approve the action" not in path.read_text()


async def test_mixed_choice_and_free_text_records_only_reference(store, tmp_path):
    runner, _ = make(store, tmp_path)
    chat = runner.chat("worker")
    runner.escalate(chat.id, "Choose?", [{"label": "SQLite"}, {"label": "Postgres"}], multiple=True)
    runner.send(chat.id, "- SQLite\n- MAIN_PRIVATE_SENTINEL is a private aside")
    await wait_until(settled(store, runner, chat.id))
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    assert "answered in chat" in state and "SQLite" not in state and "MAIN_PRIVATE_SENTINEL" not in state


async def test_choice_label_inside_free_text_does_not_reach_another_seat(store, tmp_path):
    runner, backend = make(store, tmp_path, None, bot("worker"), bot("helper"))
    room = runner.create_room("Engineering", ["worker", "helper"])
    seat = store.create_task("worker", "seat", kind="seat", room_id=room["id"], status=Status.IDLE)
    store.update_task(seat.id, session_id="existing")
    seat = store.get_task(seat.id)
    _, primed = runner._with_updates(seat, runner.crew.bots["worker"], "first")
    store.set_primed(seat.id, primed)
    chat = runner.chat("worker")
    runner.escalate(chat.id, "Which database?", [{"label": "SQLite"}, {"label": "Postgres"}])
    runner.send(chat.id, "Do not use:\nSQLite\nUse Postgres instead.")
    await wait_until(settled(store, runner, chat.id))
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    prompt, _ = runner._with_updates(seat, runner.crew.bots["worker"], "next")
    assert "answered in chat on " in state and f"(task {chat.id})" in state
    assert "SQLite" not in state and "Postgres" not in state and "SQLite" not in prompt
    assert "Record the decision in your working state, distilled, with no private detail." in backend.opened[0].turns[-1]


async def test_full_teammate_turn_leaves_question_open_for_later_user_answer(store, tmp_path):
    runner, _ = make(store, tmp_path, None, bot("worker"), bot("helper"))
    chat = runner.chat("worker")
    runner.escalate(chat.id, "What should we use?")
    store.update_task(chat.id, status=Status.NEEDS_INPUT)
    runner._escalated.discard(chat.id)
    runner.send(chat.id, "I suggest PostgreSQL", sender="helper")
    await wait_until(settled(store, runner, chat.id))
    assert store.get_task(chat.id).status == Status.IDLE
    path = runner.crew.memory.root / "worker" / "working-state" / "state.md"
    assert "What should we use?" in path.read_text()
    runner.send(chat.id, "SQLite, MAIN_PRIVATE_SENTINEL")
    await wait_until(settled(store, runner, chat.id))
    state = path.read_text()
    decisions = state.split("## Decisions", 1)[1].split("## Commitments", 1)[0]
    assert "What should we use?" in decisions and "answered in chat" in decisions
    assert "MAIN_PRIVATE_SENTINEL" not in state and "I suggest PostgreSQL" not in state
    assert "What should we use?" not in state.split("## Open questions", 1)[1].split("## Elsewhere", 1)[0]


def test_handoff_closes_questions_without_treating_idle_as_done(store, tmp_path):
    runner, _ = make(store, tmp_path)
    task = runner.chat("worker")
    runner.escalate(task.id, "Still open?")
    runner._escalated.discard(task.id)
    runner._set(task.id, status=Status.IDLE)
    path = runner.crew.memory.root / "worker" / "working-state" / "state.md"
    assert "Still open?" in path.read_text()
    runner.handoff(task.id, "Done", [], "", "")
    assert "Still open?" not in path.read_text()
