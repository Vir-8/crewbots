"""The daemon as a login service, its log, pausing the crew, and keeping the Mac awake only while work runs."""

import asyncio
import json
import plistlib
import subprocess
import sys

import pytest
from fakes import FakeBackend, make_crew, wait_until

from crewbots import service as svc
from crewbots.runner import Runner
from crewbots.store import Status, Store

FAKE_LAUNCHCTL = """
import json, os, sys
log = os.environ["LAUNCHCTL_LOG"]
with open(log, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "print":
    if os.environ.get("LAUNCHCTL_RUNNING"):
        print("state = running\\n\\tpid = 4242")
    else:
        sys.exit(113)  # not loaded
"""


@pytest.fixture
def launchctl(tmp_path, monkeypatch):
    """A fake launchctl that records its calls, and LaunchAgents in a temp folder: no real login item is touched."""
    script = tmp_path / "launchctl.py"
    script.write_text(FAKE_LAUNCHCTL)
    log = tmp_path / "launchctl.log"
    monkeypatch.setenv("LAUNCHCTL_LOG", str(log))  # LaunchAgents is under the test's own home (conftest.py)
    calls = lambda: [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []  # noqa: E731
    return (sys.executable, str(script)), calls


def test_the_launch_agent_runs_the_crews_daemon_at_login_and_after_a_crash(tmp_path):
    agent = svc.plist(tmp_path / "crew", 8765, label="app.crewbots.test", executable=tmp_path / "bin" / "crewbots")

    assert agent["ProgramArguments"] == [str(tmp_path / "bin" / "crewbots"), "--home", str(tmp_path / "crew"),
                                         "serve", "--port", "8765"]
    assert agent["RunAtLoad"] is True and agent["KeepAlive"] == {"SuccessfulExit": False}
    path = agent["EnvironmentVariables"]["PATH"].split(":")
    assert "/usr/local/bin" in path and str(svc.Path.home() / ".local/bin") in path  # where uv, claude, codex live
    assert agent["StandardErrorPath"] == str(tmp_path / "home" / ".crewbots" / "logs" / "daemon.log")


def test_install_writes_the_agent_and_loads_it(tmp_path, launchctl, monkeypatch):
    command, calls = launchctl
    monkeypatch.setenv("LAUNCHCTL_RUNNING", "1")

    state = svc.install(tmp_path / "crew", 8799, label="app.crewbots.test", launchctl=command)

    written = plistlib.loads(svc.plist_path("app.crewbots.test").read_bytes())
    assert written["Label"] == "app.crewbots.test" and written["ProgramArguments"][-1] == "8799"
    assert ["bootstrap", f"gui/{svc.os.getuid()}", str(svc.plist_path("app.crewbots.test"))] in calls()
    assert (state.installed, state.running, state.pid) == (True, True, 4242)


def test_uninstall_unloads_and_removes_it(tmp_path, launchctl):
    command, calls = launchctl
    svc.install(tmp_path / "crew", 8799, label="app.crewbots.test", launchctl=command)

    state = svc.uninstall(label="app.crewbots.test", launchctl=command)

    assert not svc.plist_path("app.crewbots.test").exists()
    assert ["bootout", f"gui/{svc.os.getuid()}/app.crewbots.test"] in calls()
    assert (state.installed, state.running) == (False, False)


def test_reinstalling_replaces_what_is_loaded(tmp_path, launchctl):
    command, calls = launchctl
    svc.install(tmp_path / "crew", 8799, label="app.crewbots.test", launchctl=command)
    svc.install(tmp_path / "crew", 8800, label="app.crewbots.test", launchctl=command)

    verbs = [c[0] for c in calls() if c[0] in ("bootstrap", "bootout")]
    assert verbs == ["bootstrap", "bootout", "bootstrap"]


# Pausing


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


async def test_a_paused_crew_starts_nothing_until_resumed(store, tmp_path):
    backend = FakeBackend()
    runner = Runner(store, make_crew(tmp_path), {"fake": backend})
    runner.pause()

    task = runner.submit("worker", "x")
    await asyncio.sleep(0.05)
    assert backend.specs == [] and store.get_task(task.id).status is Status.QUEUED

    runner.resume()
    await wait_until(lambda: store.get_task(task.id).status is Status.IDLE)
    assert len(backend.specs) == 1


async def test_pausing_lets_running_turns_finish(store, tmp_path):
    gate = asyncio.Event()
    backend = FakeBackend(gate=gate)
    runner = Runner(store, make_crew(tmp_path), {"fake": backend})
    task = runner.submit("worker", "x")
    await wait_until(lambda: backend.active == 1)

    runner.pause()
    gate.set()

    await wait_until(lambda: store.get_task(task.id).status is Status.IDLE)


async def test_a_restart_waits_only_for_turns_under_way(store, tmp_path):
    gate = asyncio.Event()
    backend = FakeBackend(gate=gate)
    runner = Runner(store, make_crew(tmp_path), {"fake": backend}, max_parallel=1)
    running = runner.submit("worker", "one")
    waiting = runner.submit("worker", "two")  # waits for the only slot
    await wait_until(lambda: backend.active == 1)

    runner.restart_when_idle()
    assert runner.turns_running() == 1 and not await runner.idle(0.05, poll_s=0.01)
    gate.set()
    assert await runner.idle(2, poll_s=0.01)
    assert store.get_task(running.id).status is Status.IDLE
    assert store.get_task(waiting.id).status is Status.QUEUED and len(backend.specs) == 1  # none started meanwhile


async def test_pausing_during_a_restart_is_kept_for_after_it_is_called_off(store, tmp_path):
    runner = Runner(store, make_crew(tmp_path), {"fake": FakeBackend()})
    runner.restart_when_idle()
    runner.pause()
    assert runner.paused
    runner.cancel_restart()
    assert runner.paused  # still paused, as asked
    runner.resume()
    task = runner.submit("worker", "x")
    await wait_until(lambda: store.get_task(task.id).status is Status.IDLE)


async def test_pause_and_resume_are_announced(store, tmp_path):
    runner = Runner(store, make_crew(tmp_path), {"fake": FakeBackend()})
    with runner.bus.subscribe() as queue:
        runner.pause()
        runner.resume()
        states = [queue.get_nowait()["crew"]["paused"] for _ in range(2)]

    assert states == [True, False]


# Keeping awake


async def test_the_mac_is_kept_awake_while_tasks_run_and_only_then(store, tmp_path):
    gate = asyncio.Event()
    backend = FakeBackend(gate=gate)
    hold = (sys.executable, "-c", "import time; time.sleep(60)")  # stands in for `caffeinate -i`
    runner = Runner(store, make_crew(tmp_path), {"fake": backend}, keep_awake=hold)

    task = runner.submit("worker", "x")
    await wait_until(lambda: backend.active == 1)
    holder = runner._awake
    assert holder is not None and holder.poll() is None
    assert holder.args[-2:] == ["-w", str(svc.os.getpid())]  # it lets go if the daemon dies

    gate.set()
    await wait_until(lambda: store.get_task(task.id).status is Status.IDLE and not runner.running())
    assert runner._awake is None
    await asyncio.to_thread(holder.wait, 5)
    assert holder.returncode is not None


def test_the_daemon_starts_a_fresh_log_past_its_size(tmp_path):
    log = tmp_path / "daemon.log"
    log.write_text("old line\n" * 20)
    script = ("import sys; from pathlib import Path; from crewbots import service; "
              f"print('rotated', service.rotate_log(max_bytes=100, log=Path({str(log)!r})), flush=True)")
    with open(log, "a") as out:  # the daemon's output is the log, as launchd gives it
        subprocess.run([sys.executable, "-c", script], stdout=out, stderr=out, check=True)

    assert (tmp_path / "daemon.log.1").read_text().startswith("old line")
    assert log.read_text() == "rotated True\n"  # what it says next goes to the fresh one


def test_the_log_is_left_alone_when_it_isnt_the_daemons_output(tmp_path):
    log = tmp_path / "daemon.log"
    log.write_text("x" * 1000)

    assert not svc.rotate_log(max_bytes=10, log=log)  # pytest holds this process's output, not the log
    assert not (tmp_path / "daemon.log.1").exists()
