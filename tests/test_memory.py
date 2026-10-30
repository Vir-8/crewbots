"""Memory: notes a bot keeps across tasks, and recall over the crew's record."""

import runpy
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fakes import ScriptedCrew, bot, make_crew, wait_until

from crewbots import prompts
from crewbots.crew import Crew
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.memory import CORE_CHARS, INDEX_LIMIT, RECENT_CHARS, STATE_CHARS, Memory, MemoryError
from crewbots.runner import Runner
from crewbots.store import Status, Store


@pytest.fixture
def memory(tmp_path):
    return Memory(tmp_path)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


# Notes


def test_a_note_is_a_file_and_the_index_follows(memory, tmp_path):
    note = memory.remember("engineer", "Uses pnpm, not npm", "The lockfile is pnpm-lock.yaml; npm breaks it.", "t_1 (engineer)")

    assert note.path == tmp_path / "memory" / "engineer" / "notes" / "uses-pnpm-not-npm.md"
    assert "source: t_1 (engineer)" in note.path.read_text()
    index = (tmp_path / "memory" / "engineer" / "MEMORY.md").read_text()
    assert "- [Uses pnpm, not npm](notes/uses-pnpm-not-npm.md): The lockfile is pnpm-lock.yaml; npm breaks it." in index


def test_the_same_title_updates_the_note_instead_of_duplicating_it(memory):
    first = memory.remember("engineer", "Test command", "pytest -q", "t_1")
    memory.remember("engineer", "Test command", "uv run pytest -q", "t_2")

    notes = memory.notes("engineer")
    assert len(notes) == 1 and notes[0].body == "uv run pytest -q" and notes[0].created == first.created


def test_forgetting_removes_the_note_and_its_line(memory, tmp_path):
    memory.remember("engineer", "Old fact", "no longer true", "t_1")

    memory.forget("engineer", "Old fact")

    assert memory.notes("engineer") == []
    assert "Old fact" not in (tmp_path / "memory" / "engineer" / "MEMORY.md").read_text()
    with pytest.raises(MemoryError, match="no note named"):
        memory.forget("engineer", "Old fact")


def test_a_note_edited_by_hand_is_what_the_bot_sees(memory):
    note = memory.remember("chief", "Release day", "Fridays", "t_1")
    note.path.write_text(note.path.read_text().replace("Fridays", "Thursdays, since October"))

    assert "Thursdays, since October" in memory.search(["chief"], "release day")[0]["text"]


def test_a_note_whose_front_matter_isnt_a_mapping_is_read_as_having_none(memory):
    note = memory.remember("chief", "Release day", "Fridays", "t_1")
    note.path.write_text("---\njust a line\n---\nThursdays\n")

    assert [(n.title, n.body) for n in memory.notes("chief")] == [("release-day", "Thursdays")]


def test_the_prompt_lists_the_bots_notes_and_the_crews(memory):
    memory.remember("engineer", "Mine", "only mine", "t_1")
    memory.remember("crew", "Everyone's", "for everyone", "t_2")
    memory.remember("reviewer", "Not mine", "the reviewer's", "t_3")

    prompt = memory.prompt("engineer")

    assert "## Your notes" in prompt and "\n- Mine" in prompt and "only mine" not in prompt  # titles, not bodies
    assert f"Files in {memory.root / 'engineer' / 'notes'}: read one" in prompt
    assert "## The crew's notes" in prompt and "\n- Everyone's" in prompt
    assert "Not mine" not in prompt
    assert memory.prompt("critic").startswith("## The crew's notes")


def test_a_long_memory_lists_the_newest_and_points_at_the_rest(memory):
    for n in range(INDEX_LIMIT + 3):
        memory.remember("engineer", f"Fact {n}", f"detail {n}", "t")

    assert "… and 3 older notes" in memory.prompt("engineer")


def test_notes_need_a_title_and_scopes_stay_in_their_folder(memory):
    with pytest.raises(MemoryError):
        memory.remember("engineer", "  ", "x", "t")
    with pytest.raises(MemoryError, match="scope"):
        memory.remember("../escape", "x", "y", "t")


def test_every_bot_is_told_about_memory_and_shown_its_notes(tmp_path):
    crew = make_crew(tmp_path, bot("engineer"))
    crew.memory.remember("engineer", "Uses pnpm", "not npm", "t_1")

    prompt = crew.system_prompt(crew.bots["engineer"])

    assert prompts.text("memory-guide", tmp_path) in prompt and "\n- Uses pnpm\n" in prompt


# The tools


async def test_bots_remember_forget_and_recall_through_their_tools(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    earlier = store.create_task("engineer", "Fix the login race")
    store.append_event(earlier.id, kind="text", data={"text": "We decided to wait for the redirect instead of sleeping."})
    now = store.create_task("engineer", "Something new")
    tools = CrewTools(crew.runner, now.id, can_delegate=False)

    assert (await tools.call("remember", {"title": "Login tests", "body": "wait, never sleep", "shared": True})).startswith(
        "Remembered: Login tests")
    assert [n.title for n in crew.runner.crew.memory.notes("crew")] == ["Login tests"]
    found = await tools.call("recall", {"query": "redirect sleeping"})
    assert earlier.id in found and "«redirect»" in found
    assert (await tools.call("forget", {"name": "Login tests", "shared": True})) == "Forgot: Login tests."
    with pytest.raises(ToolRefused, match="no note named"):
        await tools.call("forget", {"name": "Login tests", "shared": True})
    assert [e.data["action"] for e in store.events(now.id) if e.kind == "memory"] == ["remember", "forget"]


# Core memory, daily logs and a task's state


def test_core_memory_is_pinned_and_kept_short(memory, tmp_path):
    assert memory.core("engineer") is None
    (tmp_path / "memory" / "engineer").mkdir(parents=True)
    (tmp_path / "memory" / "engineer" / "CORE.md").write_text("The user ships on Tuesdays.\n" + "x" * CORE_CHARS)

    core = memory.core("engineer")
    assert core.startswith("The user ships on Tuesdays.") and len(core) == CORE_CHARS + 2 and core.endswith("\n…")


def test_the_daily_log_gets_a_line_at_a_time_and_a_session_sees_two_days(memory, tmp_path):
    memory.log("engineer", "t_1 Add CSV export: done,\n with tests")
    memory.log("engineer", "t_2 Fix login: done")
    today = datetime.now().astimezone().date()
    yesterday = (today - timedelta(days=1)).isoformat()
    folder = tmp_path / "memory" / "engineer" / "daily"
    (folder / f"{yesterday}.md").write_text(f"# {yesterday}\n\n- 17:00 t_0 Plan the export: done\n")
    (folder / f"{(today - timedelta(days=2)).isoformat()}.md").write_text("# older\n\n- 09:00 too old\n")

    log = (folder / f"{today.isoformat()}.md").read_text().splitlines()
    assert log[0] == f"# {today.isoformat()}" and log[2].endswith("t_1 Add CSV export: done, with tests")
    recent = memory.recent("engineer")
    assert recent.index("Plan the export") < recent.index("Add CSV export") and "too old" not in recent


def test_a_busy_day_starts_a_session_with_only_its_newest_lines(memory, tmp_path):
    today = datetime.now().astimezone().date()
    yesterday = (today - timedelta(days=1)).isoformat()
    folder = tmp_path / "memory" / "engineer" / "daily"
    folder.mkdir(parents=True)
    (folder / f"{yesterday}.md").write_text(f"# {yesterday}\n\n- 17:00 t_0 Planned the export\n")
    for n in range(200):
        memory.log("engineer", f"t_{n} Task {n}: " + "done " * 20)

    recent = memory.recent("engineer")

    assert len(recent) <= RECENT_CHARS + 100 and recent.startswith("(Older lines are left out: `recall` finds them.)")
    assert f"# {today.isoformat()}" in recent and "t_199 Task 199" in recent and "t_0 Task 0:" not in recent
    assert "Planned the export" not in recent  # yesterday's are older still


def test_a_tasks_state_is_written_whole_and_kept_short(memory):
    memory.set_state("engineer", "t_0123456789", "Goal: CSV export.\nPR: acme/web#412")
    memory.set_state("engineer", "t_0123456789", "Goal: CSV export.\nPR: acme/web#413")
    assert memory.state("engineer", "t_0123456789") == "Goal: CSV export.\nPR: acme/web#413"
    with pytest.raises(MemoryError, match="under"):
        memory.set_state("engineer", "t_0123456789", "x" * (STATE_CHARS + 1))
    with pytest.raises(MemoryError, match="bad task id"):
        memory.set_state("engineer", "../escape", "x")


def test_logs_and_notes_are_searched_all_words_first(memory):
    memory.log("engineer", "t_1 Add CSV export: done")
    memory.log("engineer", "t_2 Export to PDF: done")
    memory.remember("crew", "Exports", "CSV exports go through the reports API.", "t_1")

    both = memory.search(["engineer", "crew"], "csv export")  # the PDF line has only one of the words
    assert [(h["kind"], h["text"]) for h in both] == [
        ("log", both[0]["text"]), ("note", "Exports: CSV exports go through the reports API.")]
    assert both[0]["text"].endswith("t_1 Add CSV export: done")
    assert [h["text"] for h in memory.search(["engineer"], "pdf invoices")][0].endswith("Export to PDF: done")
    assert memory.search(["engineer"], "a") == []


async def test_sessions_start_from_core_logs_and_state_and_bots_keep_state(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("engineer"))
    (tmp_path / "memory" / "crew").mkdir(parents=True)
    (tmp_path / "memory" / "crew" / "CORE.md").write_text("Ship on Tuesdays.")
    task = crew.runner.submit("engineer", "start")
    await wait_until(lambda: not crew.runner.running())
    tools = CrewTools(crew.runner, task.id, can_delegate=False)
    assert "Kept this task's state" in await tools.call("update_state", {"state": "Goal: CSV export. PR acme/web#412"})
    crew.runner.handoff(task.id, "Added the export", [], "the PDF one", "")
    store.update_task(task.id, session_id=None)  # as after a rollover: a fresh session
    crew.runner.send(task.id, "carry on")
    await wait_until(lambda: len(crew.specs_for("engineer")) == 2 and not crew.runner.running())

    first, fresh = (s.system_prompt for s in crew.specs_for("engineer"))
    assert "# Core memory" in first and "## The crew's\n\nShip on Tuesdays." in first
    assert "# Where this task stands" not in first and "# Lately" not in first
    assert "Goal: CSV export. PR acme/web#412" in fresh and f"{task.id} start: Added the export Open: the PDF one" in fresh
    hits = await tools.call("recall", {"query": "export pdf"})
    assert "log (engineer" in hits and "Added the export" in hits


# Recall


def test_recall_finds_what_was_said_and_leaves_out_the_asking_task(store):
    a = store.create_task("chief", "Upgrade to React 19")
    store.append_event(a.id, kind="escalation", data={"question": "Replace legacy-charts with recharts?"})
    b = store.create_task("chief", "Charts again")

    hits = store.search("recharts legacy", exclude_task=b.id)

    assert [h["task_id"] for h in hits] == [a.id] and hits[0]["kind"] == "escalation"
    assert store.search("recharts", exclude_task=a.id) == []


def test_recall_falls_back_to_any_word_when_none_match_all(store):
    a = store.create_task("chief", "Add CSV export")

    assert [h["task_id"] for h in store.search("csv parquet export")] == [a.id]


def test_ids_paths_and_parts_of_names_are_found_anywhere_in_the_text(store):
    a = store.create_task("engineer", "Add CSV export")
    store.append_event(a.id, kind="handoff", data={"done": "Added web/src/ExportButton.tsx; PR acme/web#412",
                                                   "output": [], "open": "", "needs": ""})
    store.create_task("engineer", "Export the button styles")

    for query in ("ExportButt", "web/src/Export", "acme/web#412", "portButto"):
        assert [h["task_id"] for h in store.search(query)][:1] == [a.id], query


def test_titles_and_handoffs_outrank_chatter_and_newer_outranks_older(store):
    said = store.create_task("chief", "Weekly planning")
    store.append_event(said.id, kind="message", data={"from": "user", "text": "the ingest worker crashed again"})
    titled = store.create_task("engineer", "Ingest worker crash")
    assert [h["task_id"] for h in store.search("ingest worker crash")][0] == titled.id

    old = store.create_task("chief", "Release day")
    store.append_event(old.id, kind="handoff", data={"done": "We release on Fridays", "output": [], "open": "",
                                                     "needs": ""})
    for table in ("search", "trigrams"):
        store.db.execute(f"update {table} set at = '2026-06-01T00:00:00+00:00' where task_id = ?", (old.id,))
    new = store.create_task("chief", "Release day")
    store.append_event(new.id, kind="handoff", data={"done": "We release on Tuesdays", "output": [], "open": "",
                                                     "needs": ""})
    assert list(dict.fromkeys(h["task_id"] for h in store.search("which day do we release")))[:2] == [new.id, old.id]


def test_a_task_with_all_the_words_across_its_messages_comes_first(store):
    rate = store.create_task("engineer", "Add rate limiting to the public API")
    cache = store.create_task("engineer", "Cache GitHub responses")
    store.append_event(cache.id, kind="message", data={"from": "user", "text": "The inbox hits GitHub's rate limit"})
    assert [h["task_id"] for h in store.search("github rate limit cache")][0] == cache.id
    assert rate.id in [h["task_id"] for h in store.search("github rate limit cache")]


def test_recalls_are_logged_to_evaluate_from_real_use(store, tmp_path):
    runner = Runner(store, make_crew(tmp_path, bot("engineer")), {"fake": object()})
    found = store.create_task("engineer", "Add CSV export")
    asking = store.create_task("engineer", "Where's the export?")
    runner.recall(asking.id, "csv export")
    assert store.recall_log()[0] | {"at": ""} == {"id": 1, "at": "", "task_id": asking.id, "bot": "engineer",
                                                  "query": "csv export", "hits": [found.id]}


def test_a_chat_recalls_across_its_bots_conversations_and_a_thread_its_own(store, tmp_path):
    runner = Runner(store, make_crew(tmp_path, bot("worker")), {"fake": object()})
    chat = runner.chat("worker")
    thread = store.create_task("worker", "build it", status=Status.IDLE)
    for task in (chat, thread):
        store.append_event(task.id, kind="message", data={"from": "user", "text": "the pelican budget is 40 dollars"})
    assert {h["task_id"] for h in runner.recall(chat.id, "pelican budget")} == {chat.id, thread.id}
    assert {h["task_id"] for h in runner.recall(thread.id, "pelican budget")} == {chat.id}  # a thread has its own


def test_the_recall_evaluation_stays_high():
    evaluation = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "recall_eval.py"))
    with tempfile.TemporaryDirectory() as folder:
        runner, ids = evaluation["build"](Path(folder))
        score = evaluation["evaluate"](runner, ids, evaluation["CASES"])
        runner.store.close()
    assert score["hit@1"] >= 0.95 and score["mrr"] >= 0.95, score


def test_tool_noise_isnt_searchable(store):
    a = store.create_task("engineer", "x")
    store.append_event(a.id, kind="tool_call", data={"name": "shell", "input": {"command": "grep zebra"}})

    assert store.search("zebra") == []


def test_crews_from_before_memory_have_none(tmp_path):
    crew = Crew(home=tmp_path, bots={}, team_files={})

    assert crew.memory.prompt("anyone") is None
    assert not (tmp_path / "memory").exists()

