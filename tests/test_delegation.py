"""Delegation, end to end through the runner. The fake backend plays the bots: it calls the same runner methods the
crewbots MCP tools reach through the daemon, as the task its run token belongs to."""

import asyncio

import pytest
from fakes import FakeBackend, ScriptedCrew, bot, git_repo, make_crew, wait_until

from crewbots.backends import Access
from crewbots.events import Done
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


async def test_a_reviewer_reads_the_worktree_of_the_work_it_reviews(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer", access=Access.WORKSPACE_WRITE), bot("reviewer"))
    state = {}

    def plan(spec, me):
        if spec.prompt == "go":
            state["work"] = crew.runner.delegate(me.id, "engineer", "change it")
        elif "engineer" in spec.prompt and "review" not in state:
            state["review"] = crew.runner.delegate(me.id, "reviewer", "review it", review_of=state["work"].id)
        return [Done(final_text="ok")]

    crew.script["chief"] = plan
    top = crew.runner.submit("chief", "go", workspace=str(repo))
    await wait_until(lambda: "review" in state and crew.settled(state["review"].id)() and crew.settled(top.id)())

    work = store.get_task(state["work"].id)
    assert crew.specs_for("reviewer")[0].cwd == tmp_path / "wts" / work.worktree_path.split("/wts/")[1]


async def test_only_coordinators_delegate_and_never_to_a_coordinator(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("deputy", can_delegate=True), bot("engineer"))
    worker_task = store.create_task("engineer", "x")
    chief_task = store.create_task("chief", "y")

    with pytest.raises(NotAllowed, match="cannot delegate"):
        crew.runner.delegate(worker_task.id, "engineer", "do my work")
    with pytest.raises(NotAllowed, match="is a coordinator"):
        crew.runner.delegate(chief_task.id, "deputy", "manage this")


async def test_delegation_stops_at_the_limit(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"), max_children=2, gate=asyncio.Event())
    parent = store.create_task("chief", "y")
    crew.runner.delegate(parent.id, "engineer", "a")
    crew.runner.delegate(parent.id, "engineer", "b")

    with pytest.raises(NotAllowed, match="stop condition"):
        crew.runner.delegate(parent.id, "engineer", "c")
    await crew.runner.shutdown()


async def test_a_bot_can_only_touch_tasks_it_delegated(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, chief(), bot("engineer"))
    mine = store.create_task("chief", "mine")
    theirs = store.create_task("chief", "theirs")
    other_child = store.create_task("engineer", "x", parent_id=theirs.id, created_by="chief")

    with pytest.raises(NotAllowed, match="not delegated by this task"):
        crew.runner.message_child(mine.id, other_child.id, "hi")
    with pytest.raises(NotAllowed, match="not delegated by this task"):
        crew.runner.delegate(mine.id, "engineer", "review", review_of=other_child.id)


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
                     store.get_task(state["eng"].id).result == "built with: use postgres")
    await wait_until(crew.settled(top.id))

    escalation = next(m for m in messages(store, top.id) if "escalated" in m["text"])
    assert "postgres or sqlite?" in escalation["text"] and state["eng"].id in escalation["text"]
    assert [e.data for e in store.events(state["eng"].id) if e.kind == "escalation"] == [{"question": "postgres or sqlite?"}]
    assert messages(store, state["eng"].id)[-1] == {"from": "chief", "text": "use postgres"}
    final_report = [m["text"] for m in messages(store, top.id) if "thread(s) you opened or delegated finished" in m["text"]][-1]
    assert "built with: use postgres" in final_report


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
