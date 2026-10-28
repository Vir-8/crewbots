"""Long-lived sessions in the runner: one per active task, reused across turns, closed when idle, cancelled or done."""

import asyncio
import dataclasses

import pytest
from fakes import bot, make_crew, wait_until

from crewbots.backends import RunSpec
from crewbots.events import Done, SessionStarted, Text
from crewbots.runner import MCP_SERVER_NAME, Runner
from crewbots.store import Status, Store


class FakeSession:
    def __init__(self, backend, spec: RunSpec):
        self.backend = backend
        self.spec = spec
        self.session_id = spec.resume_session_id or f"s-{len(backend.opened)}"
        self.turns: list[str] = []
        self.closed = False
        self.die_after_turn = False
        self.fail_next = False

    @property
    def alive(self) -> bool:
        return not self.closed

    async def turn(self, prompt: str):
        self.turns.append(prompt)
        if self.backend.gate:
            await self.backend.gate.wait()
        yield SessionStarted(session_id=self.session_id)
        if self.fail_next:
            self.fail_next = False
            yield Done(final_text="", is_error=True, error="it broke")
            return
        yield Text(text=f"did: {prompt}")
        yield Done(final_text=f"did: {prompt}")
        if self.die_after_turn:
            self.closed = True

    async def close(self):
        self.closed = True


class SessionBackend:
    name = "fake"

    def __init__(self, fail_open=False):
        self.opened: list[FakeSession] = []
        self.one_shot: list[RunSpec] = []
        self.fail_open = fail_open
        self.gate: asyncio.Event | None = None
        self.sessions_die = False

    async def open(self, spec: RunSpec) -> FakeSession:
        if self.fail_open:
            raise RuntimeError("no app-server here")
        session = FakeSession(self, spec)
        session.die_after_turn = self.sessions_die
        self.opened.append(session)
        return session

    async def run(self, spec: RunSpec):
        self.one_shot.append(spec)
        yield Done(final_text="one-shot")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def make(store, tmp_path, backend=None, *bots, **kw):
    backend = backend or SessionBackend()
    runner = Runner(store, make_crew(tmp_path, *bots), {"fake": backend}, api_url="http://127.0.0.1:8765/v1", **kw)
    return runner, backend


def settled(store, runner, task_id):
    return lambda: not store.get_task(task_id).active and task_id not in runner.running()


@pytest.mark.parametrize("old_primed", [None, {"role": "old"}])
def test_existing_room_session_receives_updated_setting(store, tmp_path, old_primed):
    runner, _ = make(store, tmp_path, None, bot("worker"), bot("helper"))
    room = runner.create_room("Discussion", ["worker", "helper"])
    seat = store.create_task("worker", "Discussion (worker)", kind="seat", room_id=room["id"], status=Status.IDLE)
    store.update_task(seat.id, session_id="existing-session")
    if old_primed is not None:
        store.set_primed(seat.id, old_primed)

    prompt, primed = runner._with_updates(store.get_task(seat.id), runner.crew.bots["worker"], "[helper]: thanks")
    assert "## Group chat setting" in prompt and '"thanks"' in prompt and "NO_REPLY" in prompt
    assert primed is not None and "room-setting" in primed
    store.set_primed(seat.id, primed)
    assert runner._with_updates(store.get_task(seat.id), runner.crew.bots["worker"], "next") == ("next", None)


async def test_follow_ups_reuse_the_same_session(store, tmp_path):
    runner, backend = make(store, tmp_path)
    task = runner.submit("worker", "first")
    await wait_until(settled(store, runner, task.id))
    runner.send(task.id, "second")
    await wait_until(lambda: len(backend.opened[0].turns) == 2 and settled(store, runner, task.id)())

    assert len(backend.opened) == 1 and backend.opened[0].turns == ["first", "second"]
    assert backend.opened[0].spec.prompt == ""  # each turn brings its own prompt
    assert store.get_task(task.id).result == "did: second"
    assert backend.one_shot == []


async def test_what_changes_in_a_bots_instructions_reaches_its_session_in_the_turn_once(store, tmp_path):
    runner, backend = make(store, tmp_path, None, bot(), bot("helper"))
    runner.crew.memory.remember("worker", "Standups", "Standups are at 9.", source="test")
    task = runner.submit("worker", "first")  # a new session starts from the instructions as they are: nothing to add
    await wait_until(settled(store, runner, task.id))
    runner.send(task.id, "second")  # nothing changed since
    await wait_until(lambda: len(backend.opened[0].turns) == 2 and settled(store, runner, task.id)())

    runner.crew.memory.remember("worker", "Deploys", "Deploys go out on Tuesdays.", source="test")
    worker = dataclasses.replace(bot(), instructions="You are the worker. Be brief.")
    runner.reload(make_crew(tmp_path, worker, bot("helper"), team={"rules.md": "Ask before merging."}))
    backend.opened[0].fail_next = True
    runner.send(task.id, "third")  # this turn fails, so the next hears the changes too
    await wait_until(lambda: len(backend.opened[0].turns) == 3 and settled(store, runner, task.id)())
    runner.send(task.id, "fourth")
    await wait_until(lambda: len(backend.opened[0].turns) == 4 and settled(store, runner, task.id)())
    runner.send(task.id, "fifth")  # heard once it went through
    await wait_until(lambda: len(backend.opened[0].turns) == 5 and settled(store, runner, task.id)())

    first, second, third, fourth, fifth = backend.opened[0].turns
    assert (first, second, fifth) == ("first", "second", "fifth")
    for turn, message in ((third, "third"), (fourth, "fourth")):
        assert turn.startswith("<crew-updates>") and turn.rstrip().endswith(message)
        assert "## Your role card\n\nYou are the worker. Be brief." in turn
        assert "## team/rules.md\n\nAsk before merging." in turn
        assert "Deploys: Deploys go out on Tuesdays." in turn and "Standups" in turn  # the notes' index, as it is now
        assert "## The crew" not in turn  # what didn't change isn't repeated
    assert "Deploys" not in backend.opened[0].spec.system_prompt  # the session's own instructions stay as they began


async def test_a_session_token_works_while_the_session_is_open_and_not_after(store, tmp_path):
    runner, backend = make(store, tmp_path)
    task = runner.submit("worker", "x")
    await wait_until(settled(store, runner, task.id))
    token = backend.opened[0].spec.mcp_servers[MCP_SERVER_NAME].env["CREWBOTS_TOKEN"]

    assert runner.task_for_token(token).id == task.id  # between turns, still the task's
    await runner.cancel(task.id)
    assert runner.task_for_token(token) is None
    assert backend.opened[0].closed


async def test_an_idle_session_is_closed_and_the_next_message_resumes_it(store, tmp_path):
    runner, backend = make(store, tmp_path, idle_timeout_s=0.2)
    task = runner.submit("worker", "first")
    await wait_until(settled(store, runner, task.id))
    await wait_until(lambda: backend.opened[0].closed, timeout=5)

    assert runner.open_sessions() == []
    runner.send(task.id, "later")
    await wait_until(lambda: len(backend.opened) == 2 and settled(store, runner, task.id)())
    assert backend.opened[1].spec.resume_session_id == "s-0"


async def test_a_session_that_died_is_reopened_from_its_id(store, tmp_path):
    runner, backend = make(store, tmp_path)
    backend.sessions_die = True  # each session's process ends after its first turn
    task = runner.submit("worker", "first")
    await wait_until(settled(store, runner, task.id))

    runner.send(task.id, "again")
    await wait_until(lambda: len(backend.opened) == 2 and settled(store, runner, task.id)())

    assert backend.opened[1].spec.resume_session_id == backend.opened[0].session_id
    assert store.get_task(task.id).status is Status.IDLE


async def test_when_a_session_wont_open_the_turn_runs_on_its_own(store, tmp_path):
    runner, backend = make(store, tmp_path, SessionBackend(fail_open=True))
    task = runner.submit("worker", "x")
    await wait_until(settled(store, runner, task.id))

    assert store.get_task(task.id).result == "one-shot"
    assert any("couldn't keep a session open" in e.data.get("message", "") for e in store.events(task.id))


async def test_sessions_can_be_turned_off(store, tmp_path):
    runner, backend = make(store, tmp_path, sessions=False)
    task = runner.submit("worker", "x")
    await wait_until(settled(store, runner, task.id))

    assert backend.opened == [] and len(backend.one_shot) == 1


async def test_past_the_limit_the_least_recently_used_idle_session_is_closed(store, tmp_path):
    runner, backend = make(store, tmp_path, None, bot("a"), bot("b"), bot("c"), max_sessions=2)
    for name in ("a", "b", "c"):
        task = runner.submit(name, f"work for {name}")
        await wait_until(settled(store, runner, task.id))

    assert len(runner.open_sessions()) == 2
    assert backend.opened[0].closed and not backend.opened[2].closed


async def test_cancelling_mid_turn_closes_the_session(store, tmp_path):
    backend = SessionBackend()
    backend.gate = asyncio.Event()
    runner, _ = make(store, tmp_path, backend)
    task = runner.submit("worker", "forever")
    await wait_until(lambda: len(backend.opened) == 1 and backend.opened[0].turns)

    await runner.cancel(task.id)

    assert backend.opened[0].closed and runner.open_sessions() == []
    assert store.get_task(task.id).status is Status.CANCELLED


async def test_shutdown_closes_every_session(store, tmp_path):
    runner, backend = make(store, tmp_path, None, bot("a"), bot("b"))
    for name in ("a", "b"):
        task = runner.submit(name, "x")
        await wait_until(settled(store, runner, task.id))

    await runner.shutdown()

    assert all(s.closed for s in backend.opened) and runner.open_sessions() == []


class InProcessBackend(SessionBackend):
    """Like the Agent SDK backend: hosts the crew tools itself, and its sessions can stop a turn and stay open."""

    in_process_tools = True

    async def open(self, spec):
        session = await super().open(spec)
        session.interruptible = True
        return session


async def test_an_in_process_backend_gets_the_crew_tools_as_python_not_a_server(store, tmp_path):
    from crewbots.crew_tools import CrewTools

    runner, backend = make(store, tmp_path, InProcessBackend(), bot("chief", can_delegate=True))
    task = runner.submit("chief", "x")
    await wait_until(settled(store, runner, task.id))

    spec = backend.opened[0].spec
    assert isinstance(spec.crew, CrewTools) and spec.crew.task_id == task.id and spec.crew.can_delegate
    assert MCP_SERVER_NAME not in spec.mcp_servers


async def test_cancelling_an_interruptible_session_keeps_it_warm(store, tmp_path):
    backend = InProcessBackend()
    backend.gate = asyncio.Event()
    runner, _ = make(store, tmp_path, backend)
    task = runner.submit("worker", "forever")
    await wait_until(lambda: len(backend.opened) == 1 and backend.opened[0].turns)

    await runner.cancel(task.id)
    backend.gate.set()
    runner.send(task.id, "do something else")
    await wait_until(lambda: len(backend.opened[0].turns) == 2 and settled(store, runner, task.id)())

    assert len(backend.opened) == 1 and not backend.opened[0].closed  # the same session took the next turn


async def test_status_says_how_full_a_chats_context_is(store, tmp_path):
    class Measured(FakeSession):
        async def context_percent(self):
            return 41.26

    class MeasuredBackend(SessionBackend):
        async def open(self, spec):
            session = Measured(self, spec)
            self.opened.append(session)
            return session

    backend = MeasuredBackend()
    runner, _ = make(store, tmp_path, backend, bot(context_window=200_000))
    task = runner.submit("worker", "hi")
    await wait_until(settled(store, runner, task.id))

    status = await runner.status(task.id)

    assert backend.opened[0].spec.context_window == 200_000  # the session compacts within the bot's window
    assert status["context_window"] == 200_000
    assert status["open"] and status["session_id"] == "s-0" and status["context_percent"] == 41.3
    assert status["rollover_percent"] == 70.0 and status["plan"] == "fake" and status["turns"] == 1
    await runner.shutdown()
    assert (await runner.status(task.id))["context_percent"] == 41.3  # as last measured, once it's closed


def test_lately_and_shared_state_reach_open_seat_once(store, tmp_path):
    from crewbots.working_state import edit

    runner, _ = make(store, tmp_path, None, bot("worker"), bot("helper"))
    room = runner.create_room("Discussion", ["worker", "helper"])
    seat = store.create_task("worker", "Discussion", kind="seat", room_id=room["id"], status=Status.IDLE)
    store.update_task(seat.id, session_id="session")
    seat = store.get_task(seat.id)
    _, initial = runner._with_updates(seat, runner.crew.bots["worker"], "first")
    store.set_primed(seat.id, initial)
    runner.crew.memory.log("worker", "New log line")
    edit(runner.crew.memory.root / "worker" / "working-state" / "state.md", "User's rules", "add", "Ask before merging")
    prompt, primed = runner._with_updates(seat, runner.crew.bots["worker"], "next")
    assert "New log line" in prompt and "Ask before merging" in prompt
    store.set_primed(seat.id, primed)
    assert runner._with_updates(seat, runner.crew.bots["worker"], "again") == ("again", None)


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


async def test_private_answer_after_first_sentence_never_reaches_seat(store, tmp_path):
    runner, _ = make(store, tmp_path, None, bot("worker"), bot("helper"))
    room = runner.create_room("Engineering", ["worker", "helper"])
    seat = store.create_task("worker", "seat", kind="seat", room_id=room["id"], status=Status.IDLE)
    store.update_task(seat.id, session_id="existing")
    seat = store.get_task(seat.id)
    _, primed = runner._with_updates(seat, runner.crew.bots["worker"], "first")
    store.set_primed(seat.id, primed)
    chat = runner.chat("worker")
    runner.escalate(chat.id, "Which direction?")
    runner.send(chat.id, "Use SQLite. MAIN_PRIVATE_SENTINEL must stay in this chat.")
    await wait_until(settled(store, runner, chat.id))
    prompt, _ = runner._with_updates(seat, runner.crew.bots["worker"], "next")
    assert "Use SQLite." not in prompt and "MAIN_PRIVATE_SENTINEL" not in prompt
    assert "MAIN_PRIVATE_SENTINEL" not in (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()


async def test_choices_and_thirty_automatic_decisions_keep_only_newest(store, tmp_path):
    runner, _ = make(store, tmp_path)
    chat = runner.chat("worker")
    for i in range(30):
        runner.escalate(chat.id, f"question-{i:02d}?", [{"label": "SQLite"}, {"label": "Postgres"}])
        runner.send(chat.id, "SQLite")
        await wait_until(settled(store, runner, chat.id))
    text = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    decisions = text.split("## Decisions", 1)[1].split("## Commitments", 1)[0]
    assert decisions.count("SQLite") == 12
    assert "question-29?" in decisions and "question-18?" in decisions and "question-17?" not in decisions


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


def test_one_hundred_store_questions_leave_room_for_user_rule(store, tmp_path):
    from crewbots.working_state import QUESTION_LIMIT, edit

    runner, _ = make(store, tmp_path)
    for i in range(100):
        task = store.create_task("worker", f"work {i}", status=Status.NEEDS_INPUT)
        store.append_event(task.id, kind="escalation", data={"question": f"Question number {i:03d}?"})
    assert runner._sync_working_state("worker")
    path = runner.crew.memory.root / "worker" / "working-state" / "state.md"
    edit(path, "User's rules", "add", "Ask before merging")
    state = path.read_text()
    questions = state.split("## Open questions", 1)[1].split("## Elsewhere", 1)[0].strip()
    assert len(questions) <= QUESTION_LIMIT
    assert "Question number 099?" in questions and "Question number 000?" not in questions
    assert "Ask before merging" in state


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
    runner.send(seat.id, "- SQLite\n- Postgres", sender="user")
    await wait_until(settled(store, runner, seat.id))
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    assert "Which databases?" in state and "SQLite, Postgres" in state


async def test_open_session_reopens_when_another_bot_is_added(store, tmp_path):
    runner, backend = make(store, tmp_path, None, bot("worker"))
    chat = runner.say("worker", "hello")
    await wait_until(settled(store, runner, chat.id))
    runner.reload(make_crew(tmp_path, bot("worker"), bot("helper")))
    runner.send(chat.id, "next")
    await wait_until(lambda: len(backend.opened) == 2 and settled(store, runner, chat.id)())
    assert backend.opened[0].closed
    assert tmp_path / "memory" / "helper" / "working-state" in backend.opened[1].spec.deny_read


@pytest.mark.parametrize("terminal", [Status.FAILED, Status.CANCELLED])
def test_unanswered_permission_is_removed_when_task_ends(store, tmp_path, terminal):
    runner, _ = make(store, tmp_path)
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


async def test_user_answer_after_teammate_reply_records_decision(store, tmp_path):
    runner, _ = make(store, tmp_path)
    chat = runner.chat("worker")
    runner.escalate(chat.id, "Which database?")
    store.update_task(chat.id, status=Status.NEEDS_INPUT)
    store.append_event(chat.id, kind="message", data={"from": "helper", "text": "I suggest PostgreSQL"})
    runner.send(chat.id, "SQLite")
    await wait_until(settled(store, runner, chat.id))
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    decisions = state.split("## Decisions", 1)[1].split("## Commitments", 1)[0]
    assert "Which database?" in decisions and "answered in chat" in decisions and "SQLite" not in decisions
    assert "PostgreSQL" not in state


@pytest.mark.parametrize("answer", [
    "Use SQLite; MAIN_PRIVATE_SENTINEL must remain in this chat",
    "MAIN_PRIVATE_SENTINEL is private; use SQLite",
    "Use SQLite\nMAIN_PRIVATE_SENTINEL is private",
])
async def test_free_text_never_reaches_seat_and_answering_session_gets_nudge(store, tmp_path, answer):
    runner, backend = make(store, tmp_path, None, bot("worker"), bot("helper"))
    room = runner.create_room("Engineering", ["worker", "helper"])
    seat = store.create_task("worker", "seat", kind="seat", room_id=room["id"], status=Status.IDLE)
    store.update_task(seat.id, session_id="existing")
    seat = store.get_task(seat.id)
    _, primed = runner._with_updates(seat, runner.crew.bots["worker"], "first")
    store.set_primed(seat.id, primed)
    chat = runner.chat("worker")
    runner.escalate(chat.id, "Which direction?")
    runner.send(chat.id, answer)
    await wait_until(settled(store, runner, chat.id))
    prompt, _ = runner._with_updates(seat, runner.crew.bots["worker"], "next")
    assert "MAIN_PRIVATE_SENTINEL" not in prompt and answer not in prompt
    state = (runner.crew.memory.root / "worker" / "working-state" / "state.md").read_text()
    assert "MAIN_PRIVATE_SENTINEL" not in state and answer not in state
    assert "answered in chat on " in state and f"(task {chat.id})" in state
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
    assert "What should we use?" in state and "answered in chat" in state
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
