"""Delegation, end to end through the runner. The fake backend plays the bots: it calls the same runner methods the
crewbots MCP tools reach through the daemon, as the task its run token belongs to."""

import asyncio
import dataclasses
import json
from pathlib import Path

import pytest
from fakes import FakeBackend, ScriptedCrew, bot, git_repo, make_crew, wait_until
from fastapi.testclient import TestClient

from crewbots import worktree
from crewbots.api import create_app
from crewbots.backends import Access
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done
from crewbots.projects import Project
from crewbots.runner import MCP_SERVER_NAME, NotAllowed, Runner
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def chief(**kw):
    return bot("chief", can_delegate=True, **kw)


def messages(store, task_id):
    return [e.data for e in store.events(task_id) if e.kind == "message"]


async def test_resuming_finished_child_obeys_both_concurrency_caps(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"), gate=asyncio.Event())
    parent = store.create_task("chief", "lead", status=Status.IDLE)
    child = store.create_task("engineer", "finished", parent_id=parent.id, created_by="chief", status=Status.IDLE)
    crew.runner.max_children = 1
    active = store.create_task("chief", "thread", parent_id=parent.id, created_by="chief", status=Status.HELD)
    with pytest.raises(NotAllowed, match="unfinished child"):
        crew.runner.message_child(parent.id, child.id, "resume")
    store.update_task(active.id, status=Status.IDLE)
    crew.runner.max_out = 1
    elsewhere = store.create_task("engineer", "other", created_by="chief", status=Status.NEEDS_INPUT)
    with pytest.raises(NotAllowed, match="delegated tasks"):
        crew.runner.message_child(parent.id, child.id, "resume")
    assert messages(store, child.id) == []
    store.update_task(elsewhere.id, status=Status.IDLE)
    assert crew.runner.message_child(parent.id, child.id, "resume").status == Status.QUEUED
    await crew.runner.shutdown()


async def test_the_chief_delegates_and_hears_back_once_everything_is_done(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"), bot("writer"))
    children = []

    def plan(spec, me):
        if spec.prompt.startswith("ship it"):
            children.append(crew.runner.delegate(me.id, "engineer", "build the thing"))
            children.append(crew.runner.delegate(me.id, "writer", "document the thing"))
            return [Done(final_text="delegated")]
        return [Done(final_text="all done: summary for the user")]

    def work(spec, me):
        crew.runner.handoff(me.id, done=f"{me.bot} finished", output=[f"{me.bot}.md"], open="", needs="review")
        return [Done(final_text=f"{me.bot} final words")]

    crew.script = {"chief": plan, "engineer": work, "writer": work}
    top = crew.runner.submit("chief", "ship it")
    await wait_until(lambda: len(crew.specs_for("chief")) == 2 and crew.settled(top.id)())

    assert [c.parent_id for c in children] == [top.id, top.id]
    assert [c.created_by for c in children] == ["chief", "chief"]
    report = messages(store, top.id)[-1]
    assert report["from"] == "crewbots"
    assert "2 thread(s) you opened or delegated finished" in report["text"]
    assert "Done: engineer finished" in report["text"] and "Output: writer.md" in report["text"]
    assert "engineer final words" in report["text"]
    assert crew.specs_for("chief")[1].resume_session_id == f"s-{top.id}"  # the chief picks up where it left off
    assert store.get_task(top.id).result == "all done: summary for the user"
    assert all(store.get_task(c.id).reported for c in children)


async def test_a_task_delegated_by_the_chief_knows_who_it_reports_to(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"))
    crew.script["chief"] = lambda spec, me: (
        [crew.runner.delegate(me.id, "engineer", "x"), Done(final_text="ok")][1:] if spec.prompt == "go" else
        [Done(final_text="done")])
    top = crew.runner.submit("chief", "go")
    await wait_until(lambda: len(crew.specs_for("chief")) == 2 and crew.settled(top.id)())

    assert "delegated to you by chief" in crew.specs_for("engineer")[0].system_prompt
    assert "This task comes from the user" in crew.specs_for("chief")[0].system_prompt


async def test_children_inherit_the_workspace_and_get_their_own_worktrees(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer", access=Access.WORKSPACE_WRITE))
    kids = []
    crew.script["chief"] = lambda spec, me: (
        [kids.append(crew.runner.delegate(me.id, "engineer", f"part {i}")) for i in range(2)] and
        [Done(final_text="ok")] if spec.prompt == "go" else [Done(final_text="done")])
    top = crew.runner.submit("chief", "go", workspace=str(repo))
    await wait_until(lambda: len(crew.specs_for("chief")) == 2 and crew.settled(top.id)())

    paths = {store.get_task(k.id).worktree_path for k in kids}
    assert len(paths) == 2 and None not in paths  # two parallel engineers, two checkouts


async def test_a_teammate_writes_in_place_only_where_the_user_said_work_goes(store, tmp_path):
    given, elsewhere, repo = tmp_path / "given", tmp_path / "elsewhere", git_repo(tmp_path / "repo")
    for folder in (given / "cuts", elsewhere):
        folder.mkdir(parents=True)
    crew = ScriptedCrew(store, tmp_path / "crew", chief(), bot("editor", access=Access.WORKSPACE_WRITE), bot("reader"))
    top = store.create_task("chief", "plan the edit", workspace=str(given))

    with pytest.raises(NotAllowed, match="hasn't given this work"):  # the delegating bot's say-so isn't enough
        crew.runner.delegate(top.id, "editor", "cut it", workspace=str(elsewhere))
    for fine in (given / "cuts", repo):  # within the work's own folder, or a repository it gets a worktree of
        crew.runner.delegate(top.id, "editor", "cut it", workspace=str(fine))
    crew.runner.delegate(top.id, "reader", "look it over", workspace=str(elsewhere))  # reading is fine anywhere
    await wait_until(lambda: not crew.runner.running())


async def test_work_on_a_project_runs_in_its_folder_which_the_user_gave_by_adding_it(store, tmp_path):
    footage, game = tmp_path / "footage", git_repo(tmp_path / "game")
    footage.mkdir()
    crew = ScriptedCrew(store, tmp_path / "crew", chief(), bot("editor", access=Access.WORKSPACE_WRITE))
    crew.runner.crew = dataclasses.replace(crew.runner.crew, projects={
        "film": Project("film", footage, "The short film.", tmp_path / "film.md"),
        "game": Project("game", game, "The game.", tmp_path / "game.md")})
    top = store.create_task("chief", "plan")  # its chat, in no project

    cut = crew.runner.delegate(top.id, "editor", "cut the trailer", project="film")  # not a repository: in place
    fix = crew.runner.delegate(top.id, "editor", "fix the jump", project="game")  # a repository: its own worktree
    plan = crew.runner.start_thread(top.id, "Plan the level", "plan it", project="game")
    with pytest.raises(NotAllowed, match="no project named 'web'"):
        crew.runner.delegate(top.id, "editor", "ship it", project="web")
    await wait_until(lambda: not crew.runner.running())

    assert footage in {s.cwd for s in crew.specs_for("editor")}
    assert store.get_task(fix.id).repo == str(game) and store.get_task(fix.id).worktree_path
    assert store.get_task(cut.id).workspace == store.get_task(plan.id).workspace.replace("game", "footage")
    assert "- film (" in crew.specs_for("editor")[0].system_prompt  # every bot sees the projects


async def test_a_reviewer_runs_the_work_in_a_throwaway_copy_of_it(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer", access=Access.WORKSPACE_WRITE), bot("reviewer"))
    state = {}

    def plan(spec, me):
        if spec.prompt == "go":
            state["work"] = crew.runner.delegate(me.id, "engineer", "change it")
        elif "engineer" in spec.prompt and "review" not in state:
            state["review"] = crew.runner.delegate(me.id, "reviewer", "review it", review_of=state["work"].id)
        return [Done(final_text="ok")]

    def engineer(spec, me):
        (spec.cwd / "README.md").write_text("changed, not committed")
        (spec.cwd / "new.txt").write_text("new")
        return [Done(final_text="done")]

    def reviewer(spec, me):
        state["saw"] = ((spec.cwd / "README.md").read_text(), (spec.cwd / "new.txt").read_text())
        (spec.cwd / "new.txt").write_text("the reviewer's own run")  # a scratch change of its own
        return [Done(final_text="ok")]

    crew.script |= {"chief": plan, "engineer": engineer, "reviewer": reviewer}
    top = crew.runner.submit("chief", "go", workspace=str(repo))
    await wait_until(lambda: "review" in state and crew.settled(state["review"].id)() and crew.settled(top.id)())

    work = store.get_task(state["work"].id)
    [spec] = crew.specs_for("reviewer")
    assert spec.cwd != Path(work.worktree_path) and spec.cwd.parent == tmp_path / "wts" / "checks"
    assert spec.access is Access.WORKSPACE_WRITE  # a read-only reviewer may run things in its copy
    assert state["saw"] == ("changed, not committed", "new")  # the work as it stands, uncommitted changes and all
    assert (Path(work.worktree_path) / "new.txt").read_text() == "new"  # the reviewer's changes never reach the work
    await wait_until(lambda: not spec.cwd.exists())  # the copy goes once the review is done


async def test_unity_work_in_a_worktree_is_reviewed_in_place_without_a_copy(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    (repo / "ProjectSettings").mkdir()
    (repo / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.12f1\n")
    worktree._git(repo, "add", "-A")
    worktree._git(repo, "commit", "-qm", "A Unity project")
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer", access=Access.WORKSPACE_WRITE), bot("reviewer"))
    lead = store.create_task("chief", "lead", status=Status.IDLE)
    work = store.create_task("engineer", "build", parent_id=lead.id, created_by="chief", status=Status.IDLE)
    wt = worktree.create(repo, work.id, root=tmp_path / "wts")
    store.update_task(work.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base, turns=1)

    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))

    [spec] = crew.specs_for("reviewer")
    assert spec.cwd == wt.path and spec.access is Access.READ_ONLY  # checked in an Editor, not a copy of its files
    assert not (tmp_path / "wts" / "checks").exists()
    await crew.runner.shutdown()


async def test_work_in_a_project_worked_in_place_shares_its_folder(store, tmp_path):
    repo = git_repo(tmp_path / "game")
    (repo / "ProjectSettings").mkdir()
    (repo / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.12f1\n")
    crew = ScriptedCrew(store, tmp_path / "crew", chief(), bot("engineer", access=Access.WORKSPACE_WRITE))
    game = Project("game", repo, "A game", tmp_path / "g.md")
    crew.runner.reload(dataclasses.replace(crew.runner.crew, projects={"game": game}))
    crew.script["engineer"] = lambda spec, me: ((spec.cwd / f"{me.id}.cs").write_text("// mine"), [Done(final_text="ok")])[1]
    lead = store.create_task("chief", "lead", status=Status.IDLE)

    work = [crew.runner.delegate(lead.id, "engineer", f"part {n}", project="game") for n in (1, 2)]
    for w in work:
        await wait_until(crew.settled(w.id))

    assert {s.cwd for s in crew.specs_for("engineer")} == {repo}  # both at once, in the folder itself
    assert all(store.get_task(w.id).worktree_path is None and store.get_task(w.id).branch is None for w in work)
    assert worktree._git(repo, "branch", "--show-current") == "main" and (repo / f"{work[0].id}.cs").exists()
    await crew.runner.shutdown()


async def test_any_bot_delegates_but_not_to_itself_or_the_chief(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("deputy", can_delegate=True), bot("engineer"), bot("writer"),
                        gate=asyncio.Event())
    worker_task = store.create_task("engineer", "x")
    chief_task = store.create_task("chief", "y")

    child = crew.runner.delegate(worker_task.id, "writer", "write the docs for it")
    assert child.bot == "writer" and child.created_by == "engineer" and child.parent_id == worker_task.id
    with pytest.raises(NotAllowed, match="that's you"):
        crew.runner.delegate(worker_task.id, "engineer", "do my work")
    assert crew.runner.delegate(chief_task.id, "deputy", "manage this").bot == "deputy"
    with pytest.raises(NotAllowed, match="is the chief"):
        crew.runner.delegate(worker_task.id, "chief", "manage this")
    await crew.runner.shutdown()


async def test_a_delegation_never_goes_back_round_the_chain(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("a"), bot("b"), gate=asyncio.Event())
    top = store.create_task("chief", "ship it")
    to_a = crew.runner.delegate(top.id, "a", "build it")
    to_b = crew.runner.delegate(to_a.id, "b", "test it")

    with pytest.raises(NotAllowed, match="go round in a circle"):
        crew.runner.delegate(to_b.id, "a", "you build this part")  # a handed this to b: b can't hand it back down
    await crew.runner.shutdown()


async def test_a_chain_of_delegations_stops_at_its_depth(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("a"), bot("b"), bot("c"), bot("d"), max_depth=3,
                        gate=asyncio.Event())
    top = store.create_task("chief", "ship it")
    one = crew.runner.delegate(top.id, "a", "1")
    two = crew.runner.delegate(one.id, "b", "2")
    three = crew.runner.delegate(two.id, "c", "3")  # three deep: the most
    own = crew.runner.start_thread(three.id, "my part", "a thread of c's own doesn't count")

    with pytest.raises(NotAllowed, match="4 delegations deep, and the most is 3"):
        crew.runner.delegate(three.id, "d", "4")
    with pytest.raises(NotAllowed, match="delegations deep"):
        crew.runner.delegate(own.id, "d", "4")
    await crew.runner.shutdown()


async def test_a_bot_has_only_so_many_delegated_tasks_out_at_once(store, tmp_path):
    gate = asyncio.Event()
    crew = ScriptedCrew(store, tmp_path, chief(), bot("a"), bot("b"), bot("c"), max_out=2, gate=gate)
    mine, other = store.create_task("a", "one"), store.create_task("a", "two")
    crew.runner.delegate(mine.id, "b", "x")
    crew.runner.delegate(other.id, "c", "y")  # across all of a's work

    with pytest.raises(NotAllowed, match="already have 2 delegated tasks"):
        crew.runner.delegate(mine.id, "c", "z")
    gate.set()  # they finish
    await wait_until(lambda: store.delegated_out("a") == 0)
    assert crew.runner.delegate(mine.id, "c", "z").bot == "c"
    await crew.runner.shutdown()


async def test_no_delegating_past_a_budget(store, tmp_path):
    from crewbots.crew import Budget

    crew = ScriptedCrew(store, tmp_path, chief(), bot("a"), bot("b", budget=Budget(tokens=10, per="day")),
                        bot("c"), gate=asyncio.Event())
    spent = store.create_task("b", "earlier")
    store.append_event(spent.id, kind="usage", data={"input_tokens": 50, "output_tokens": 5})
    mine = store.create_task("a", "work")

    with pytest.raises(NotAllowed, match="no budget for more work: b has used 55 of its 10 tokens"):
        crew.runner.delegate(mine.id, "b", "x")
    assert crew.runner.delegate(mine.id, "c", "y").bot == "c"
    await crew.runner.shutdown()


async def test_delegation_stops_at_the_limit(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"), max_children=2, gate=asyncio.Event())
    parent = store.create_task("chief", "y")
    crew.runner.delegate(parent.id, "engineer", "a")
    crew.runner.delegate(parent.id, "engineer", "b")

    with pytest.raises(NotAllowed, match="stop condition"):
        crew.runner.delegate(parent.id, "engineer", "c")
    await crew.runner.shutdown()


@pytest.mark.parametrize("action", ["delegate", "start_thread"])
@pytest.mark.parametrize("finished,unfinished,blocked", [(12, 0, False), (0, 12, True), (12, 11, False), (12, 12, True)])
async def test_child_limit_only_counts_unfinished_work(store, tmp_path, action, finished, unfinished, blocked):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"), max_out=100, gate=asyncio.Event())
    parent = store.create_task("chief", "chat", kind="chat")
    for i in range(finished):
        status = (Status.IDLE, Status.FAILED, Status.CANCELLED, Status.INTERRUPTED)[i % 4]
        store.create_task("chief", "finished", parent_id=parent.id, status=status)
    for i in range(unfinished):
        status = (Status.QUEUED, Status.RUNNING, Status.HELD, Status.NEEDS_INPUT)[i % 4]
        store.create_task("chief", "unfinished", parent_id=parent.id, status=status)

    def open_work():
        if action == "delegate":
            return crew.runner.delegate(parent.id, "engineer", "work")
        return crew.runner.start_thread(parent.id, "work", "work")

    if blocked:
        with pytest.raises(NotAllowed, match="12 unfinished"):
            open_work()
    else:
        assert open_work().parent_id == parent.id
    await crew.runner.shutdown()


async def test_a_bot_can_message_its_delegations_from_another_session(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"))
    room = crew.runner.create_room("Engineering", ["chief", "engineer"], lead="chief")
    mine = store.create_task("chief", "seat", kind="seat", room_id=room["id"])
    theirs = crew.runner.chat("chief")
    other_child = store.create_task("engineer", "x", parent_id=theirs.id, created_by="chief")

    stranger = store.create_task("engineer", "other session")
    seat_tools = CrewTools(crew.runner, mine.id, can_delegate=True)
    other_tools = CrewTools(crew.runner, stranger.id, can_delegate=True)
    assert other_child.id in [t["id"] for t in json.loads(await seat_tools.call("tasks", {}))]
    assert other_child.id not in [t["id"] for t in json.loads(await other_tools.call("tasks", {}))]
    with pytest.raises(ToolRefused, match="not delegated by this bot"):
        await other_tools.call("message", {"task_id": other_child.id, "text": "hi"})
    with pytest.raises(NotAllowed, match="not delegated by this task"):
        crew.runner.delegate(mine.id, "engineer", "review", review_of=other_child.id)
    await seat_tools.call("message", {"task_id": other_child.id, "text": "hi"})
    assert messages(store, other_child.id)[-1] == {"from": "chief", "text": "hi"}
    await crew.runner.shutdown()


async def test_tasks_includes_user_and_checkin_threads_across_sessions(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"), gate=asyncio.Event())
    chat = crew.runner.chat("engineer")
    user_thread = crew.runner.thread_for("engineer", "User's /new thread")
    checkin_thread = store.create_task("engineer", "Check-in work", created_by="crewbots", parent_id=chat.id,
                                      status=Status.IDLE)
    session = store.create_task("engineer", "other session", status=Status.IDLE)
    tools = CrewTools(crew.runner, session.id, can_delegate=False)
    ids = {t["id"] for t in json.loads(await tools.call("tasks", {}))}
    assert {user_thread.id, checkin_thread.id} <= ids
    await tools.call("message", {"task_id": checkin_thread.id, "text": "Continue"})
    assert messages(store, checkin_thread.id)[-1] == {"from": "engineer", "text": "Continue"}
    await crew.runner.shutdown()


async def test_tasks_keeps_all_unfinished_and_only_twenty_recent_finished(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief())
    chat = crew.runner.chat("chief")
    finished = [store.create_task("chief", f"finished {i}", parent_id=chat.id, status=Status.IDLE) for i in range(25)]
    unfinished = [store.create_task("chief", f"unfinished {i}", parent_id=chat.id, status=Status.HELD) for i in range(25)]
    ids = {item["id"] for item in crew.runner.delegated_by(chat.id)}
    assert {task.id for task in unfinished} <= ids
    assert len(ids & {task.id for task in finished}) == 20
    assert len(ids) == 45


async def test_an_escalation_reaches_the_chief_at_once_and_its_answer_resumes_the_worker(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"), bot("writer"))
    state = {"answered": False}

    def plan(spec, me):
        if spec.prompt == "go":
            state["eng"] = crew.runner.delegate(me.id, "engineer", "build")
            state["writer"] = crew.runner.delegate(me.id, "writer", "write")
        elif "escalated" in spec.prompt and not state["answered"]:
            state["answered"] = True
            crew.runner.message_child(me.id, state["eng"].id, "use postgres")
        return [Done(final_text="ok")]

    def engineer(spec, me):
        if spec.prompt == "build":
            crew.runner.escalate(me.id, "postgres or sqlite?")
            return [Done(final_text="waiting on a decision")]
        return [Done(final_text=f"built with: {spec.prompt}")]

    crew.script = {"chief": plan, "engineer": engineer, "writer": lambda spec, me: [Done(final_text="written")]}
    top = crew.runner.submit("chief", "go")
    await wait_until(lambda: "eng" in state and crew.settled(state["eng"].id)() and
                     "use postgres" in (store.get_task(state["eng"].id).result or ""))
    # The answer reached the engineer marked as its teammate's, not taken for the user's word.
    assert store.get_task(state["eng"].id).result.startswith("built with: From chief, your teammate")
    await wait_until(crew.settled(top.id))

    escalation = next(m for m in messages(store, top.id) if "escalated" in m["text"])
    assert "postgres or sqlite?" in escalation["text"] and state["eng"].id in escalation["text"]
    assert [e.data for e in store.events(state["eng"].id) if e.kind == "escalation"] == [{"question": "postgres or sqlite?"}]
    assert messages(store, state["eng"].id)[-1] == {"from": "chief", "text": "use postgres"}
    final_report = [m["text"] for m in messages(store, top.id) if "thread(s) you opened or delegated finished" in m["text"]][-1]
    assert "use postgres" in final_report


async def test_a_question_can_offer_choices_and_the_chief_hears_them(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"))
    state = {}

    def plan(spec, me):
        if spec.prompt == "go":
            state["eng"] = crew.runner.delegate(me.id, "engineer", "build")
        return [Done(final_text="ok")]

    def engineer(spec, me):
        crew.runner.escalate(me.id, "Which database?", [
            "Postgres", {"label": "SQLite", "description": "  one file,\n no server "}, "postgres", "Other",
            {"label": ""}, *[f"option {n}" for n in range(9)]], multiple=True)
        return [Done(final_text="asked")]

    crew.script = {"chief": plan, "engineer": engineer}
    top = crew.runner.submit("chief", "go")
    await wait_until(lambda: any("escalated" in m["text"] for m in messages(store, top.id)))

    [asked] = [e.data for e in store.events(state["eng"].id) if e.kind == "escalation"]
    assert asked["multiple"] is True and len(asked["choices"]) == 6  # no repeat, no "Other", no blank; six at most
    assert asked["choices"][:3] == [{"label": "Postgres"}, {"label": "SQLite", "description": "one file, no server"},
                                    {"label": "option 0"}]
    told = next(m["text"] for m in messages(store, top.id) if "escalated" in m["text"])
    assert "Its choices (any of them):\n- Postgres\n- SQLite: one file, no server" in told


async def test_a_top_level_escalation_waits_for_the_user(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief())
    crew.script["chief"] = lambda spec, me: (crew.runner.escalate(me.id, "merge it?"), [Done(final_text="asked")])[1]
    top = crew.runner.submit("chief", "go")
    await wait_until(crew.settled(top.id))

    assert store.get_task(top.id).status is Status.NEEDS_INPUT


async def test_run_tokens_only_work_while_their_run_lasts(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief())
    top = crew.runner.submit("chief", "go")
    await wait_until(crew.settled(top.id))
    token = crew.backend.specs[0].mcp_servers[MCP_SERVER_NAME].env["CREWBOTS_TOKEN"]

    assert crew.runner.task_for_token(token) is None


async def test_every_run_gets_the_crew_tools_and_coordinators_get_delegation(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"))
    a = crew.runner.submit("chief", "a")
    b = crew.runner.submit("engineer", "b")
    await wait_until(lambda: crew.settled(a.id)() and crew.settled(b.id)())

    envs = {s.system_prompt.split("You are the ")[1].split(".")[0]: s.mcp_servers[MCP_SERVER_NAME].env
            for s in crew.backend.specs}
    assert envs["chief"]["CREWBOTS_DELEGATE"] == "1" and envs["engineer"]["CREWBOTS_DELEGATE"] == "0"
    assert envs["chief"]["CREWBOTS_URL"] == "http://127.0.0.1:8765/v1"
    assert envs["chief"]["CREWBOTS_TOKEN"] != envs["engineer"]["CREWBOTS_TOKEN"]


async def test_without_a_daemon_bots_run_without_crew_tools(store, tmp_path):
    backend = FakeBackend()
    runner = Runner(store, make_crew(tmp_path), {"fake": backend})
    task = runner.submit("worker", "x")
    await wait_until(lambda: not store.get_task(task.id).active)

    assert MCP_SERVER_NAME not in backend.specs[0].mcp_servers


async def test_a_cancelled_chief_is_not_woken_by_its_children(store, tmp_path):
    gate = asyncio.Event()
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"))
    parent = store.create_task("chief", "y")
    store.update_task(parent.id, status=Status.CANCELLED)
    crew.backend.gate = gate
    child = crew.runner.delegate(parent.id, "engineer", "a")
    gate.set()
    await wait_until(crew.settled(child.id))

    assert store.get_task(parent.id).status is Status.CANCELLED
    assert messages(store, parent.id) == []


# Asking a teammate


async def test_a_bot_asks_a_teammate_and_hears_the_answer(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer", access=Access.WORKSPACE_WRITE, workspace=repo),
                        bot("critic", access=Access.WORKSPACE_WRITE, workspace=repo))
    asked = []

    def engineer(spec, me):
        if spec.prompt.startswith("fix the parser"):
            asked.append(crew.runner.ask(me.id, "critic", "Did we decide to keep the old date format?"))
            return [Done(final_text="asked the critic")]
        return [Done(final_text=f"thanks: {spec.prompt[:200]}")]

    crew.script = {"engineer": engineer, "critic": lambda spec, me: [Done(final_text="Yes: ISO dates stay, see t_1.")]}
    top = crew.runner.submit("engineer", "fix the parser")
    await wait_until(lambda: len(crew.specs_for("engineer")) == 2 and crew.settled(top.id)())

    question = store.get_task(asked[0].id)
    assert question.kind == "ask" and question.created_by == "engineer" and question.parent_id == top.id
    assert question.worktree_path is None  # a question gets no worktree, even from a bot that writes
    [critic_spec] = crew.specs_for("critic")
    assert critic_spec.access is Access.READ_ONLY and "engineer asks: Did we decide" in critic_spec.prompt
    assert "a question from engineer" in critic_spec.system_prompt
    report = messages(store, top.id)[-1]["text"]
    assert report.startswith("Your question was answered.")
    assert "You asked: Did we decide to keep the old date format?" in report and "Yes: ISO dates stay" in report


async def test_a_question_is_answered_not_turned_into_work(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"), bot("critic"), gate=asyncio.Event())
    top = store.create_task("engineer", "work")
    question = crew.runner.ask(top.id, "critic", "how does the cache expire?")

    for start in (lambda: crew.runner.delegate(question.id, "engineer", "x"),
                  lambda: crew.runner.ask(question.id, "engineer", "x"),
                  lambda: crew.runner.start_thread(question.id, "x", "x")):
        with pytest.raises(NotAllowed, match="answering a teammate's question"):
            start()
    with pytest.raises(NotAllowed, match="answer it yourself"):
        crew.runner.ask(top.id, "engineer", "what do I think?")
    await crew.runner.shutdown()


async def test_what_bots_do_to_each_other_is_logged_and_outlives_a_deleted_group_chat(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"), bot("critic"))
    chat = crew.runner.chat("chief")
    work = crew.runner.delegate(chat.id, "engineer", "build the export")
    crew.runner.ask(chat.id, "critic", "will the export hold up?")
    await wait_until(lambda: not crew.runner.running())
    crew.runner.message_child(chat.id, work.id, "and add a test")
    room = crew.runner.open_group(chat.id, "Export", ["engineer"])
    crew.runner.add_to_group(chat.id, "critic", room["id"])
    crew.runner.post_from(chat.id, "@critic thoughts?", room["id"])
    crew.runner.post(room["id"], "user", "carry on")  # the user's own posts aren't the log's business
    with pytest.raises(ToolRefused):
        await CrewTools(crew.runner, work.id, can_delegate=False).call("delegate", {"bot": "engineer", "goal": "me?"})
    await wait_until(lambda: not crew.runner.running())
    await crew.runner.delete_room(room["id"])

    logged = [(e["kind"], e["bot"], e["target"], e["text"]) for e in reversed(store.agent_log())]
    assert logged[:6] == [
        ("delegate", "chief", "engineer", "build the export"),
        ("ask", "chief", "critic", "will the export hold up?"),
        ("message", "chief", "engineer", "and add a test"),
        ("open_group", "chief", None, "Export: chief, engineer"),
        ("add_to_group", "chief", "critic", "Export"),
        ("post", "chief", None, "@critic thoughts?"),
    ]
    assert all(bot != "user" for _, bot, _, _ in logged)
    assert logged[6][:2] == ("refused", "engineer") and logged[6][3].startswith("delegate: that's you")
    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        page = client.get("/agent-log", params={"limit": 2}, headers={"Authorization": "Bearer t"}).json()
        older = client.get("/agent-log", params={"before": page[-1]["id"]}, headers={"Authorization": "Bearer t"}).json()
    assert len(page) == 2 and older[0]["id"] == page[-1]["id"] - 1
