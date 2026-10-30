"""A merge runs the repo's CI steps on the squashed result first; if they fail, main doesn't move."""

import pytest
from test_ci import temp  # noqa: F401
from test_push import git
from test_squash_merge import IDENT, work  # noqa: F401

from crewbots import ci, worktree
from crewbots.projects import Project
from crewbots.runner.common import MergeError
from crewbots.store import Status

WORKFLOW = "on: push\njobs:\n  t:\n    runs-on: x\n    steps:\n      - run: test -f one.txt && test -f user.txt\n"


def with_ci(runner, repo, tmp_path, *commands):
    runner.crew.projects["demo"] = Project("demo", repo, "", tmp_path / "demo.md", ci=commands)


def user_commit(repo):
    (repo / "user.txt").write_text("x")
    git(repo, "add", "user.txt")
    git(repo, *IDENT, "commit", "-qm", "user")
    return git(repo, "rev-parse", "HEAD")


def test_a_failing_step_blocks_the_merge(work, tmp_path):  # noqa: F811
    runner, store, repo, task, wt = work
    head = user_commit(repo)
    with_ci(runner, repo, tmp_path, "echo boom >&2; test -f nope.txt")
    with pytest.raises(MergeError, match=r"(?s)nothing was merged.*at step 'echo boom.*exit 1.*boom"):
        runner.merge(task.id, force=True)
    assert git(repo, "rev-parse", "main") == head
    assert git(repo, "status", "--porcelain") == ""
    assert store.get_task(task.id).merge_state == "proposed"


def test_a_passing_run_merges_the_squash_on_top_of_main(work, tmp_path):  # noqa: F811
    runner, store, repo, task, wt = work
    user_commit(repo)
    with_ci(runner, repo, tmp_path, "test -f user.txt && test -f two.txt && test -f loose.txt")  # main's and the task's
    merged = runner.merge(task.id, force=True)
    assert merged.merge_state == "merged" and git(repo, "rev-parse", "main") == merged.merged_commit


def test_a_project_in_a_subfolder_gates_the_merge_with_its_commands_run_there(work, tmp_path):  # noqa: F811
    runner, store, repo, task, wt = work
    (repo / "app").mkdir()
    (repo / "app" / "marker.txt").write_text("x")
    git(repo, "add", "app")
    git(repo, *IDENT, "commit", "-qm", "app")
    store.update_task(task.id, workspace=str(repo / "app"))  # the thread was started in the project's folder
    ran_in = tmp_path / "ran-in"
    runner.crew.projects["app"] = Project("app", repo / "app", "", tmp_path / "app.md", ci=("false",))
    with pytest.raises(MergeError, match="at step 'false'"):
        runner.merge(task.id, force=True)
    runner.crew.projects["app"] = Project("app", repo / "app", "", tmp_path / "app.md",
                                          ci=(f"test -f marker.txt && pwd > {ran_in}",))
    assert runner.merge(task.id, force=True).merge_state == "merged"
    assert ran_in.read_text().strip().endswith("/checkout/app")


def test_workflow_steps_gate_the_merge(work, tmp_path):  # noqa: F811
    runner, store, repo, task, wt = work
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text(WORKFLOW)
    git(repo, "add", ".")
    git(repo, *IDENT, "commit", "-qm", "ci")
    head = git(repo, "rev-parse", "main")
    with pytest.raises(MergeError, match="user.txt"):  # the squash has one.txt but not user.txt
        runner.merge(task.id, force=True)
    assert git(repo, "rev-parse", "main") == head


async def test_the_route_keeps_the_loop_responsive_during_a_slow_merge(work, tmp_path):  # noqa: F811
    import asyncio

    import httpx

    from crewbots.api import create_app
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "sleep 1")
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    ticking = asyncio.create_task(ticker())
    app = create_app(runner, token="admin")
    with runner.bus.subscribe() as queue:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765/v1") as client:
            response = await client.post(f"/tasks/{task.id}/merge", json={"force": True},
                                         headers={"Authorization": "Bearer admin"})
        await asyncio.sleep(0.1)
        published = [queue.get_nowait() for _ in range(queue.qsize())]
    ticking.cancel()
    assert response.status_code == 200 and response.json()["merge_state"] == "merged"
    assert ticks >= 10  # about 20 ticks fit in the second CI ran; a blocked loop would have none
    assert any(m.get("event", {}).get("kind") == "merged" for m in published)  # events from the thread reach watchers


def test_any_git_error_building_the_test_commit_refuses_the_merge(work, tmp_path, monkeypatch):  # noqa: F811
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "true")
    original = worktree._git

    def broken(cwd, *args, **kwargs):
        if "commit-tree" in args:
            raise worktree.GitError("commit-tree failed (exit code 128): no space left")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", broken)
    head = git(repo, "rev-parse", "main")
    with pytest.raises(MergeError, match="could not build the merged result.*no space left"):
        runner.merge(task.id, force=True)
    assert git(repo, "rev-parse", "main") == head and store.get_task(task.id).merge_state == "proposed"


def counting_ci(monkeypatch, during):
    """ci.check, run for real, that calls during(n) after its n-th run (as main moves while CI runs)."""
    original, runs = ci.check, []

    def check(*args, **kwargs):
        result = original(*args, **kwargs)
        runs.append(args[1])
        during(len(runs))
        return result

    monkeypatch.setattr(ci, "check", check)
    return runs


def test_main_moving_during_ci_reruns_ci_once_on_the_new_squash(work, tmp_path, monkeypatch):  # noqa: F811
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "true")
    runs = counting_ci(monkeypatch, lambda n: user_commit(repo) if n == 1 else None)
    merged = runner.merge(task.id, force=True)
    assert len(runs) == 2 and runs[0] != runs[1]  # CI ran again, on the squash over the new main
    assert merged.merge_state == "merged" and (repo / "user.txt").exists() and (repo / "two.txt").exists()
    assert git(repo, "rev-list", "--count", f"{wt.base}..main") == "2"  # the user's commit and ours


def test_main_moving_again_during_the_rerun_refuses_the_merge(work, tmp_path, monkeypatch):  # noqa: F811
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "true")
    counter = iter(range(100))

    def move(n):
        (repo / f"moved{next(counter)}.txt").write_text("x")
        git(repo, "add", ".")
        git(repo, *IDENT, "commit", "-qm", "user")

    runs = counting_ci(monkeypatch, move)
    with pytest.raises(MergeError, match="main moved while CI ran, and again during the second CI run, so nothing was merged"):
        runner.merge(task.id, force=True)
    assert len(runs) == 2 and not (repo / "two.txt").exists()
    assert store.get_task(task.id).merge_state == "proposed"


def test_the_checkout_switching_branch_during_ci_refuses_the_merge(work, tmp_path, monkeypatch):  # noqa: F811
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "true")
    main = git(repo, "rev-parse", "main")
    runs = counting_ci(monkeypatch, lambda n: git(repo, "checkout", "-q", "-b", "other"))
    with pytest.raises(MergeError, match="switched from main to other while CI ran, so nothing was merged; propose again"):
        runner.merge(task.id, force=True)
    assert len(runs) == 1  # no rerun onto a branch nobody chose
    assert git(repo, "rev-parse", "main") == main and git(repo, "rev-parse", "other") == main
    assert not (repo / "two.txt").exists() and store.get_task(task.id).merge_state == "proposed"


def second_task(runner, store, repo, tmp_path, name):
    """Another proposed worktree task in the same repo, with one committed file."""
    task = store.create_task("engineer", f"add {name}", status=Status.IDLE)
    wt = worktree.create(repo, task.id, root=tmp_path / "wts2")
    store.update_task(task.id, repo=str(repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base,
                      merge_state="proposed")
    (wt.path / name).write_text(name)
    worktree.commit(wt, f"add {name}")
    return task, wt


async def test_a_cancelled_caller_and_a_retry_merge_the_task_once(work, tmp_path):  # noqa: F811
    import asyncio
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "sleep 0.6")
    first = asyncio.create_task(runner.merge_off_loop(task.id, True))
    await asyncio.sleep(0.2)  # the worker is in CI
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(MergeError, match="already merged"):  # the retry waits for the worker, then finds it merged
        await runner.merge_off_loop(task.id, True)
    assert git(repo, "rev-list", "--count", f"{wt.base}..main") == "1"
    assert store.get_task(task.id).merge_state == "merged"


async def test_a_caller_cancelled_while_its_worker_is_queued_clears_the_merging_mark(work, tmp_path, monkeypatch):  # noqa: F811
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from crewbots.runner import merge_status
    from crewbots.runner.common import NotAllowed
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "true")
    pool = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(merge_status, "_EXECUTOR", pool)
    gate = threading.Event()
    pool.submit(gate.wait)  # the only thread is busy, so the merge worker queues
    queued = asyncio.create_task(runner.merge_off_loop(task.id, True))
    await asyncio.sleep(0.1)
    with pytest.raises(NotAllowed, match="a merge of this task is running"):
        runner.withdraw_merge(task.id, "changed my mind")
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    runner.withdraw_merge(task.id, "changed my mind")  # not refused: cancel() succeeded on the queued job
    gate.set()
    pool.shutdown(wait=True)
    assert store.get_task(task.id).merge_state is None
    assert git(repo, "rev-list", "--count", f"{wt.base}..main") == "0"  # the merge never ran


async def test_a_caller_cancelled_after_the_executor_claimed_the_job_keeps_the_mark(work, tmp_path, monkeypatch):  # noqa: F811
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from crewbots.runner import merge_status
    from crewbots.runner.common import NotAllowed
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "true")
    claimed, release = threading.Event(), threading.Event()

    class Gated(ThreadPoolExecutor):
        def submit(self, fn, *a, **kw):  # the job is claimed by a thread but held before it enters the worker
            def held():
                claimed.set()
                release.wait()
                return fn(*a, **kw)
            return super().submit(held)

    pool = Gated(max_workers=1)
    monkeypatch.setattr(merge_status, "_EXECUTOR", pool)
    running = asyncio.create_task(runner.merge_off_loop(task.id, True))
    await asyncio.get_running_loop().run_in_executor(None, claimed.wait)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    with pytest.raises(NotAllowed, match="a merge of this task is running"):
        runner.withdraw_merge(task.id, "changed my mind")
    release.set()
    pool.shutdown(wait=True)  # the worker finishes the merge
    assert store.get_task(task.id).merge_state == "merged"
    with pytest.raises(Exception, match="has no merge proposal to withdraw"):  # no longer refused as running
        runner.withdraw_merge(task.id, "too late")


async def test_a_withdraw_during_ci_is_refused(work, tmp_path):  # noqa: F811
    import asyncio

    from crewbots.runner.common import NotAllowed
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "sleep 0.6")
    merging = asyncio.create_task(runner.merge_off_loop(task.id, True))
    await asyncio.sleep(0.2)
    with pytest.raises(NotAllowed, match="a merge of this task is running; try again when it finishes"):
        runner.withdraw_merge(task.id, "changed my mind")
    with pytest.raises(NotAllowed, match="a merge of this task is running"):
        runner.drop_worktree(task.id)
    assert (await merging).merge_state == "merged"
    assert wt.base != git(repo, "rev-parse", "main")


def test_a_proposal_changed_during_ci_is_not_landed(work, tmp_path, monkeypatch):  # noqa: F811
    runner, store, repo, task, wt = work
    with_ci(runner, repo, tmp_path, "true")
    head = git(repo, "rev-parse", "main")
    counting_ci(monkeypatch, lambda n: store.update_task(task.id, merge_state=None))  # as a withdraw would
    with pytest.raises(MergeError, match="the proposal changed while CI ran, so nothing was merged"):
        runner.merge(task.id, force=True)
    assert git(repo, "rev-parse", "main") == head


async def test_two_tasks_merge_one_after_the_other_each_tested_on_its_own_result(work, tmp_path):  # noqa: F811
    import asyncio
    runner, store, repo, task, wt = work
    other, other_wt = second_task(runner, store, repo, tmp_path, "other.txt")
    log = tmp_path / "ci.log"
    with_ci(runner, repo, tmp_path, f"ls > {log}.$$ && cat {log}.$$ >> {log} && sleep 0.4 && echo done >> {log}")
    results = await asyncio.gather(runner.merge_off_loop(task.id, True), runner.merge_off_loop(other.id, True),
                                   return_exceptions=True)
    assert all(not isinstance(r, Exception) for r in results), results
    lines = log.read_text().split()
    first_done = lines.index("done")
    assert "other.txt" not in lines[:first_done] or "one.txt" not in lines[:first_done]  # one task's files per run
    assert {"one.txt", "other.txt"} <= set(lines[first_done:])  # the second run saw the first merge's result
    assert git(repo, "rev-list", "--count", f"{wt.base}..main") == "2"  # one squash commit each, in sequence
    assert (repo / "one.txt").exists() and (repo / "other.txt").exists()
