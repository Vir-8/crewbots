"""Unity Editors crewbots opens on a task's own worktree, tells it of once connected, and closes when the work is done
with. Unity's processes and the Unity MCP are stood in for."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until

from crewbots import unity, worktree
from crewbots.backends import Access, McpServer
from crewbots.runner import NotAllowed
from crewbots.runner import editors as editors_module
from crewbots.store import Status, Store

EDITOR = "/Applications/Unity/Hub/Editor/6000.3.12f1/Unity.app/Contents/MacOS/Unity"
PS = "\n".join([
    f"  101 {EDITOR} -projectpath /Users/v/Unity Games/Sky Islands -useHub -hubIPC",
    f"  102 {EDITOR} -adb2 -batchMode -name AssetImportWorker0 -projectPath /Users/v/Unity Games/Sky Islands -logFile x",
    f"  103 {EDITOR} -projectPath /w/Sky Islands-1/t_1",
    "  104 /usr/bin/python3 server.py -projectPath /nowhere",
])


def test_the_editor_for_a_project_and_the_folders_editors_have_open(tmp_path, monkeypatch):
    game = tmp_path / "game"
    (game / "ProjectSettings").mkdir(parents=True)
    (game / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.12f1\nm_EditorVersionWithRevision: x\n")
    (tmp_path / "Hub" / "6000.3.12f1" / "Unity.app").mkdir(parents=True)
    monkeypatch.setattr(unity, "HUB_EDITORS", tmp_path / "Hub")
    monkeypatch.setattr(unity, "HUB_OTHER", tmp_path / "none.json")
    assert unity.version(game) == "6000.3.12f1"
    assert unity.editor_app(game) == tmp_path / "Hub" / "6000.3.12f1" / "Unity.app"
    assert unity.editor_app(tmp_path) is None  # not a Unity project

    monkeypatch.setattr(unity, "_processes", lambda: PS)
    assert unity.editors() == {Path("/Users/v/Unity Games/Sky Islands").resolve(): 101,
                               Path("/w/Sky Islands-1/t_1").resolve(): 103}  # not its import workers


def test_where_processes_cant_be_listed_there_are_no_editors(monkeypatch):
    def sandboxed(*args, **kwargs):  # a bot's scratch daemon, in a sandbox that doesn't allow ps
        raise PermissionError(1, "Operation not permitted", "ps")

    monkeypatch.setattr(unity.subprocess, "run", sandboxed)
    assert unity.editors() == {}


@pytest.fixture
def unity_crew(store, tmp_path, monkeypatch):
    """A Unity project, an engineer's task in a worktree of it, and Unity as a dict of open Editors."""
    repo = git_repo(tmp_path / "game")
    (repo / "ProjectSettings").mkdir()
    (repo / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.12f1\n")
    worktree._git(repo, "add", "-A")
    worktree._git(repo, "commit", "-qm", "Unity")
    open_now: dict[Path, int] = {}
    said = {"connected": False}
    monkeypatch.setattr(unity, "editor_app", lambda folder: Path("/Applications/Unity.app"))
    pids = iter(range(100, 200))
    monkeypatch.setattr(unity, "open_editor", lambda app, folder: open_now.update({folder.resolve(): next(pids)}))
    monkeypatch.setattr(unity, "editors", lambda: dict(open_now))
    monkeypatch.setattr(unity, "close_editor", lambda pid: [open_now.pop(f) for f, p in list(open_now.items()) if p == pid])

    async def connected(url, name):
        return said["connected"]

    monkeypatch.setattr(unity, "connected", connected)
    monkeypatch.setattr(editors_module, "CONNECT_EVERY_S", 0.01)
    engineer = bot("engineer", access=Access.WORKSPACE_WRITE,
                   mcp={"UnityMCP": McpServer(url="http://unity.test/mcp", transport="http")})
    crew = ScriptedCrew(store, tmp_path / "crew", bot("chief", can_delegate=True), bot("lead", can_delegate=True), engineer)

    def work():
        task = store.create_task("engineer", "build", status=Status.IDLE)
        wt = worktree.create(repo, task.id, root=tmp_path / "crew" / "wts")
        return store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base)

    return crew, work, open_now, said, repo


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def told(store, task_id):
    return [e.data["text"] for e in store.events(task_id) if e.kind == "message" and e.data["from"] == "crewbots"]


async def test_a_worktree_task_gets_an_editor_and_is_told_once_its_connected(store, unity_crew):
    crew, work, open_now, said, repo = unity_crew
    task = work()

    answer = crew.runner.open_unity_editor(task.id)
    assert "Opening Unity on your worktree" in answer and list(open_now) == [Path(task.worktree_path).resolve()]
    assert "already open" in crew.runner.open_unity_editor(task.id) and len(open_now) == 1
    said["connected"] = True
    await wait_until(lambda: told(store, task.id))
    assert told(store, task.id)[0].startswith("Unity is open on your worktree and connected: set_active_instance to its "
                                              f"{task.id}@")
    await crew.runner.shutdown()


async def test_only_a_worktree_of_the_tasks_own_gets_one(store, unity_crew):
    crew, work, open_now, said, repo = unity_crew
    in_place = store.create_task("engineer", "build", workspace=str(repo), status=Status.IDLE)
    with pytest.raises(NotAllowed, match="only on a worktree of the task's own"):
        crew.runner.open_unity_editor(in_place.id)
    assert open_now == {}
    await crew.runner.shutdown()


async def test_at_most_two_at_once_an_idle_one_makes_room_and_one_closes_when_its_worktree_goes(store, unity_crew):
    crew, work, open_now, said, repo = unity_crew
    first, second, third, fourth = work(), work(), work(), work()
    crew.runner.open_unity_editor(first.id)
    crew.runner.open_unity_editor(second.id)

    assert "2 Unity Editors are open for other tasks" in crew.runner.open_unity_editor(third.id)
    assert len(open_now) == 2
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    store.db.execute("update tasks set updated_at = ? where id = ?", (old, first.id))
    crew.runner.close_idle_editors()  # nobody has used the first's for an hour
    assert list(open_now) == [Path(second.worktree_path).resolve()]
    assert told(store, third.id) == ["There's room for a Unity Editor now: ask for yours with open_unity_editor."]

    crew.runner.open_unity_editor(third.id)
    crew.runner.close_editor(Path(second.worktree_path))  # its worktree is going
    assert list(open_now) == [Path(third.worktree_path).resolve()]
    assert "Opening Unity" in crew.runner.open_unity_editor(fourth.id)
    await crew.runner.shutdown()


async def test_waiting_for_its_editor_isnt_reported_as_finished_and_the_wait_survives_a_restart(store, unity_crew):
    crew, work, open_now, said, repo = unity_crew
    lead = store.create_task("lead", "lead", status=Status.IDLE)
    task = work()
    store.update_task(task.id, parent_id=lead.id, turns=1)
    crew.runner.open_unity_editor(task.id)
    crew.runner._report_up(task.id)  # its turn ended, to wait for Unity
    assert not [e for e in store.events(lead.id) if e.kind == "message" and e.data["from"] == "crewbots"]
    assert store.chief_events() == []  # nor to the chief

    for watch in list(crew.runner._editor_watches):  # the daemon stops
        watch.cancel()
    await wait_until(lambda: not crew.runner._editor_watches)
    crew.runner.resume_editor_watches()  # and starts again
    assert crew.runner._waiting_for_editor(task.id)
    said["connected"] = True
    await wait_until(lambda: told(store, task.id))
    assert not crew.runner._waiting_for_editor(task.id)
    await crew.runner.shutdown()


async def test_an_editor_on_a_folder_of_no_task_of_ours_is_left_alone(store, unity_crew, tmp_path):
    crew, work, open_now, said, repo = unity_crew
    stranger = (tmp_path / "crew" / "wts" / "other-crew" / "t_0000000000").resolve()
    open_now[stranger] = 999  # another crew's, or opened by hand
    crew.runner.close_idle_editors()
    assert open_now == {stranger: 999}
    first, second = work(), work()
    crew.runner.open_unity_editor(first.id)
    assert "Opening Unity" in crew.runner.open_unity_editor(second.id)  # it doesn't count against ours
    await crew.runner.shutdown()
