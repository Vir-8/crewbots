import asyncio

import pytest
from fakes import FakeBackend, bot, git_repo, make_crew, wait_until

from crewbots import paths
from crewbots.backends import Access
from crewbots.events import Done, SessionStarted, Text
from crewbots.runner import NotAllowed, Runner, UnknownBot
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def runner_for(store, tmp_path, *bots, backend=None, **kw):
    backend = backend or FakeBackend()
    crew = make_crew(tmp_path, *bots, team={"standards.md": "Tests pass."})
    return Runner(store, crew, {"fake": backend}, worktree_root=tmp_path / "wts", **kw), backend


def settled(store, task_id):
    return lambda: store.get_task(task_id).status not in (Status.QUEUED, Status.RUNNING)


async def test_a_task_runs_on_its_bot_and_keeps_the_transcript(store, tmp_path):
    runner, backend = runner_for(store, tmp_path)

    task = runner.submit("worker", "say hi")
    await wait_until(settled(store, task.id))

    task = store.get_task(task.id)
    assert task.status is Status.IDLE
    assert task.result == "did: say hi"
    assert task.session_id == "s-1"
    assert (task.input_tokens, task.output_tokens, task.cost_usd) == (10, 2, 0.01)
    assert [e.kind for e in store.events(task.id)] == ["message", "turn_start", "session_started", "text", "usage", "done"]
    assert store.events(task.id)[0].data == {"from": "user", "text": "say hi"}


async def test_the_bot_gets_its_role_card_and_the_team_files(store, tmp_path):
    runner, backend = runner_for(store, tmp_path)

    task = runner.submit("worker", "x")
    await wait_until(settled(store, task.id))

    prompt = backend.specs[0].system_prompt
    assert "You are the worker." in prompt and "# team/standards.md" in prompt and "Tests pass." in prompt
    assert backend.specs[0].cwd == paths.bot_folders() / "worker"  # no workspace: its own folder, made for it


async def test_a_follow_up_resumes_the_same_session(store, tmp_path):
    runner, backend = runner_for(store, tmp_path)
    task = runner.submit("worker", "first")
    await wait_until(settled(store, task.id))

    runner.send(task.id, "second")
    await wait_until(lambda: len(backend.specs) == 2 and settled(store, task.id)())

    assert backend.specs[0].resume_session_id is None
    assert backend.specs[1].resume_session_id == "s-1"
    assert store.get_task(task.id).result == "did: second"


async def test_messages_sent_mid_run_wait_and_go_in_together(store, tmp_path):
    gate = asyncio.Event()
    runner, backend = runner_for(store, tmp_path, backend=FakeBackend(gate=gate))
    task = runner.submit("worker", "first")
    await wait_until(lambda: backend.active == 1)

    runner.send(task.id, "also this")
    runner.send(task.id, "and this")
    gate.set()
    await wait_until(lambda: len(backend.specs) == 2 and settled(store, task.id)())

    assert backend.max_active == 1  # never two runs of one task at once
    assert backend.specs[1].prompt == "also this\n\nand this"


async def test_a_task_with_messages_waiting_is_never_shown_idle(store, tmp_path):
    gate = asyncio.Event()
    runner, backend = runner_for(store, tmp_path, backend=FakeBackend(gate=gate))
    task = runner.submit("worker", "first")
    await wait_until(lambda: backend.active == 1)
    seen = []
    with runner.bus.subscribe() as queue:
        runner.send(task.id, "more")
        gate.set()
        await wait_until(lambda: len(backend.specs) == 2 and settled(store, task.id)())
        while not queue.empty():
            message = queue.get_nowait()
            if message["type"] == "task":
                seen.append(message["task"]["status"])

    assert seen[-1] == "idle" and "idle" not in seen[:-1]


async def test_runs_across_tasks_share_the_parallel_limit(store, tmp_path):
    gate = asyncio.Event()
    runner, backend = runner_for(store, tmp_path, backend=FakeBackend(gate=gate), max_parallel=2)
    tasks = [runner.submit("worker", f"t{i}") for i in range(3)]
    await wait_until(lambda: backend.active == 2)
    await asyncio.sleep(0.05)

    assert backend.active == 2
    assert sorted(store.get_task(t.id).status for t in tasks) == [Status.QUEUED, Status.RUNNING, Status.RUNNING]
    gate.set()
    for t in tasks:
        await wait_until(settled(store, t.id))
    assert backend.max_active == 2


async def test_a_failed_run_is_failed_and_can_be_retried(store, tmp_path):
    def flaky(spec):
        if spec.prompt == "try":
            return [SessionStarted(session_id="s-9"), Done(final_text="", is_error=True, error="usage limit")]
        return [Text(text="ok"), Done(final_text="ok")]

    runner, backend = runner_for(store, tmp_path, backend=FakeBackend(reply=flaky))
    task = runner.submit("worker", "try")
    await wait_until(settled(store, task.id))
    assert store.get_task(task.id).status is Status.FAILED
    assert store.get_task(task.id).error == "usage limit"

    runner.send(task.id, "again")
    await wait_until(lambda: len(backend.specs) == 2 and settled(store, task.id)())

    assert store.get_task(task.id).status is Status.IDLE
    assert store.get_task(task.id).error is None
    assert backend.specs[1].resume_session_id == "s-9"


async def test_a_backend_that_crashes_fails_the_task(store, tmp_path):
    def crash(spec):
        raise RuntimeError("boom")

    runner, _ = runner_for(store, tmp_path, backend=FakeBackend(reply=crash))
    task = runner.submit("worker", "x")
    await wait_until(settled(store, task.id))

    assert store.get_task(task.id).status is Status.FAILED
    assert "boom" in store.get_task(task.id).error


async def test_a_run_that_never_finishes_fails(store, tmp_path):
    runner, _ = runner_for(store, tmp_path, backend=FakeBackend(reply=lambda spec: [Text(text="hm")]))
    task = runner.submit("worker", "x")
    await wait_until(settled(store, task.id))

    assert store.get_task(task.id).error == "the run ended without a result"


async def test_cancel_stops_the_run(store, tmp_path):
    runner, backend = runner_for(store, tmp_path, backend=FakeBackend(gate=asyncio.Event()))
    task = runner.submit("worker", "forever")
    await wait_until(lambda: backend.active == 1)

    await runner.cancel(task.id)

    assert store.get_task(task.id).status is Status.CANCELLED
    assert backend.closed == 1 and runner.running() == []


async def test_cancel_begin_failure_releases_guard_and_cancels_run(store, tmp_path, monkeypatch):
    gate = asyncio.Event()
    runner, backend = runner_for(store, tmp_path, backend=FakeBackend(gate=gate))
    task = runner.submit("worker", "first")
    await wait_until(lambda: backend.active == 1)
    run = runner._runs[task.id]
    clear_pending = store.clear_pending

    def fail(_task_id):
        raise OSError("storage failure")

    monkeypatch.setattr(store, "clear_pending", fail)
    with pytest.raises(OSError, match="storage failure"):
        await runner.cancel(task.id)
    with pytest.raises(asyncio.CancelledError):
        await run
    assert task.id not in runner._cancelling
    monkeypatch.setattr(store, "clear_pending", clear_pending)
    runner.send(task.id, "again")
    await wait_until(lambda: backend.active == 1)
    await runner.cancel(task.id)
    assert store.get_task(task.id).status is Status.CANCELLED


@pytest.mark.parametrize("active", [False, True])
async def test_cancel_blocks_new_runs_until_cleanup_finishes(store, tmp_path, monkeypatch, active):
    gate = asyncio.Event()
    runner, backend = runner_for(store, tmp_path, backend=FakeBackend(gate=gate) if active else None)
    task = runner.submit("worker", "first") if active else store.create_task("worker", "finished", status=Status.IDLE)
    if active:
        await wait_until(lambda: backend.active == 1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_close(_task_id):
        entered.set()
        await release.wait()

    monkeypatch.setattr(runner.browser, "close_task", blocked_close)
    cancelling = asyncio.create_task(runner.cancel(task.id))
    await asyncio.wait_for(entered.wait(), 2)
    try:
        with pytest.raises(NotAllowed, match="being cancelled"):
            runner.send(task.id, "start again")
        with pytest.raises(NotAllowed, match="being cancelled"):
            runner._start(task.id, "start again")
        assert len(backend.specs) == int(active)
        assert set(runner.running()) == ({task.id} if active else set())
    finally:
        release.set()
        await cancelling
    assert task.id not in runner.running()
    gate.set()
    runner.send(task.id, "start again")
    await wait_until(settled(store, task.id))
    assert backend.specs[-1].prompt == "start again"


async def test_shutdown_interrupts_running_tasks(store, tmp_path):
    runner, backend = runner_for(store, tmp_path, backend=FakeBackend(gate=asyncio.Event()))
    task = runner.submit("worker", "forever")
    await wait_until(lambda: backend.active == 1)

    await runner.shutdown()

    assert store.get_task(task.id).status is Status.INTERRUPTED


async def test_a_writing_bot_in_a_repo_gets_its_own_worktree(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    runner, backend = runner_for(store, tmp_path, bot(access=Access.WORKSPACE_WRITE, workspace=repo))

    def write(spec):
        (spec.cwd / "new.txt").write_text("made by the bot\n")
        return [Done(final_text="wrote it")]

    backend.reply = write
    task = runner.submit("worker", "write a file")
    await wait_until(settled(store, task.id))

    task = store.get_task(task.id)
    assert task.branch == f"crewbots/{task.id}"
    assert backend.specs[0].cwd == tmp_path / "wts" / task.worktree_path.split("/wts/")[1]
    assert not (repo / "new.txt").exists()
    assert "+made by the bot" in runner.changes(task.id)
    assert "worktree" in [e.kind for e in store.events(task.id)]


async def test_watchers_hear_about_the_worktree_while_the_task_runs(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    runner, backend = runner_for(store, tmp_path, bot(access=Access.WORKSPACE_WRITE, workspace=repo),
                                 backend=FakeBackend(gate=asyncio.Event()))
    with runner.bus.subscribe() as queue:
        task = runner.submit("worker", "x")
        await wait_until(lambda: backend.active == 1)
        updates = []
        while not queue.empty():
            message = queue.get_nowait()
            if message["type"] == "task":
                updates.append(message["task"])

    assert any(u["worktree_path"] and u["status"] == "running" for u in updates)
    await runner.cancel(task.id)


async def test_a_follow_up_stays_in_the_same_worktree(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    runner, backend = runner_for(store, tmp_path, bot(access=Access.WORKSPACE_WRITE, workspace=repo))
    task = runner.submit("worker", "one")
    await wait_until(settled(store, task.id))
    runner.send(task.id, "two")
    await wait_until(lambda: len(backend.specs) == 2 and settled(store, task.id)())

    assert backend.specs[0].cwd == backend.specs[1].cwd != repo


async def test_a_writing_bot_never_works_in_the_crews_own_folder(store, tmp_path):
    home = tmp_path / "crew"
    (home / "team").mkdir(parents=True)
    runner, backend = runner_for(store, home, bot("editor", access=Access.WORKSPACE_WRITE), bot("reader"))
    own = runner.submit("editor", "tidy up")  # no folder named on its card: it works in its own
    look = runner.submit("reader", "look")
    await wait_until(settled(store, own.id))
    await wait_until(settled(store, look.id))
    assert {s.cwd for s in backend.specs} == {paths.bot_folders() / "editor", paths.bot_folders() / "reader"}

    for folder in (home, home / "team", tmp_path):  # one that holds the crew's settings, it doesn't start in
        task = runner.submit("editor", "edit the cards", workspace=str(folder))
        await wait_until(settled(store, task.id))
        assert store.get_task(task.id).status is Status.FAILED
        assert "crew's own settings" in store.get_task(task.id).error


async def test_read_only_bots_and_plain_folders_work_in_place(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    folder = tmp_path / "footage"
    folder.mkdir()
    runner, backend = runner_for(
        store, tmp_path / "crew", bot("reader", workspace=repo),
        bot("editor", access=Access.WORKSPACE_WRITE, workspace=folder),
    )
    a = runner.submit("reader", "look")
    b = runner.submit("editor", "cut")
    await wait_until(settled(store, a.id))
    await wait_until(settled(store, b.id))

    assert {s.cwd for s in backend.specs} == {repo, folder}
    assert runner.changes(a.id) is None


async def test_a_task_can_name_its_own_workspace(store, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    runner, backend = runner_for(store, tmp_path)

    task = runner.submit("worker", "x", workspace=str(other))
    await wait_until(settled(store, task.id))

    assert backend.specs[0].cwd == other


async def test_a_missing_workspace_fails_the_task(store, tmp_path):
    runner, _ = runner_for(store, tmp_path)

    task = runner.submit("worker", "x", workspace=str(tmp_path / "nope"))
    await wait_until(settled(store, task.id))

    assert "does not exist" in store.get_task(task.id).error


def test_an_unknown_bot_is_refused(store, tmp_path):
    runner, _ = runner_for(store, tmp_path)

    with pytest.raises(UnknownBot):
        runner.submit("ghost", "x")
