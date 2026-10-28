"""No Chrome is launched: a tiny Python child exercises inherited CDP fds 3/4."""

import asyncio
import fnmatch
import json
import os
import signal
import sys
from urllib.parse import urlsplit

import pytest
from test_browser import FakeCDP, pipe_pair

from crewbots.browser import Browser
from crewbots.browser_cdp import CDP, BrowserError
from crewbots.browser_process import CHROME, Profiles, launch


async def test_pipe_launcher_remaps_descriptors_and_frames_cdp(tmp_path):
    script = tmp_path / "fake_chrome.py"
    script.write_text('''import fcntl, json, os, stat
extra = []
for fd in range(5, 64):
    try:
        fcntl.fcntl(fd, fcntl.F_GETFD)
        extra.append(fd)
    except OSError:
        pass
assert stat.S_ISFIFO(os.fstat(3).st_mode) and stat.S_ISFIFO(os.fstat(4).st_mode)
with os.fdopen(3, 'rb', buffering=0) as source, os.fdopen(4, 'wb', buffering=0) as target:
    buffer = b''
    while chunk := source.read(65536):
        buffer += chunk
        while b'\\0' in buffer:
            raw, buffer = buffer.split(b'\\0', 1)
            request = json.loads(raw)
            result = {'extra_fds': extra, 'echo': request['params']}
            response = json.dumps({'id': request['id'], 'result': result}).encode() + b'\\0'
            while response:
                written = target.write(response[:1024])
                response = response[written:]
''')
    profile = tmp_path / "chrome-recorded"
    profile.mkdir()
    with (tmp_path / "stderr.log").open("wb") as log:
        process, transport = await launch([sys.executable, str(script)], log, profile)
        cdp = CDP(transport)
        try:
            results = await asyncio.gather(*(cdp.call("Echo", {"text": "x" * 100000, "n": n}) for n in range(2)))
            assert [r["echo"]["n"] for r in results] == [0, 1]
            assert all(r["extra_fds"] == [] and len(r["echo"]["text"]) == 100000 for r in results)
            from crewbots.browser_process import process_info, read_owner
            pid, start = read_owner(profile)
            assert pid == process.pid and start == process_info(pid).start_time
        finally:
            await cdp.close()
            async with asyncio.timeout(3):
                await process.wait()
        assert process.returncode == 0


async def test_browser_launch_has_pipe_and_no_debugging_port(tmp_path, monkeypatch):
    from crewbots import browser as module

    fake = FakeCDP()
    worker = None
    peer = None
    command = []

    class Process:
        returncode = None

        def terminate(self):
            self.returncode = 0

        async def wait(self):
            return self.returncode

    async def start(argv, log, profile):
        nonlocal worker, peer
        command.extend(argv)
        transport, peer = await pipe_pair()
        fake.socket = peer
        worker = asyncio.create_task(fake.handle(peer))
        return Process(), transport

    monkeypatch.setattr(module, "launch", start)
    service = Browser(tmp_path / "browser")
    try:
        await service.action("task", tmp_path, False, "open", {"url": "http://localhost"})
        assert "--remote-debugging-pipe" in command
        assert not any(arg.startswith(("--proxy-server", "--proxy-bypass-list", "--no-proxy-server")) for arg in command)
        tab = service.sessions["task"]
        context = next(c["params"] for c in fake.calls if c["method"] == "Target.createBrowserContext")
        assert context["proxyServer"] == tab.proxy.address
        assert context["proxyBypassList"] == "<-loopback>" and tab.proxy.server.is_serving()
        rules = next(arg.split("=", 1)[1] for arg in command if arg.startswith("--host-resolver-rules="))
        rules = [rule.strip() for rule in rules.split(",")]
        assert "MAP * ~NOTFOUND" in rules
        excluded = [rule.removeprefix("EXCLUDE ") for rule in rules if rule.startswith("EXCLUDE ")]
        proxy_host = urlsplit(context["proxyServer"]).hostname
        assert any(fnmatch.fnmatchcase(proxy_host, pattern) for pattern in excluded)
        assert not any(fnmatch.fnmatchcase("external.example", pattern) for pattern in excluded)
        assert "--enable-features=NetworkServiceInProcess2" in command
        target = next(c["params"] for c in fake.calls if c["method"] == "Target.createTarget")
        assert target["browserContextId"] == tab.context and target["url"] == "about:blank"
        assert not any("remote-debugging-port" in arg or "remote-debugging-address" in arg for arg in command)
        assert not hasattr(service, "port")
        assert not list((tmp_path / "browser").rglob("DevToolsActivePort"))
    finally:
        await service.close()
        if peer:
            await peer.close()
        if worker:
            await worker


def write_owner(profile, pid, started):
    (profile / "crewbots-owner.json").write_text(json.dumps({"pid": pid, "start_time": started}))


async def test_sweep_removes_only_old_ownerless_profiles(tmp_path, monkeypatch, caplog):
    import time

    from crewbots import browser_process as module

    root = tmp_path / "browser"
    old, recent, linked = (root / name for name in ("chrome-old", "chrome-recent", "chrome-linked"))
    for profile in (old, recent, linked):
        profile.mkdir(parents=True)
    (linked / module.OWNER_FILE).symlink_to(root / "missing")
    old_time = time.time() - 3700
    os.utime(old, (old_time, old_time))
    os.utime(linked, (old_time, old_time))
    monkeypatch.setattr(module.os, "kill", lambda *args: pytest.fail("must not signal ownerless processes"))
    profiles = Profiles(root)
    await profiles.acquire()
    profiles.close()
    assert not old.exists() and recent.exists() and linked.exists()
    assert "owner file missing" in caplog.text


async def test_sweep_logs_parent_of_skipped_process(tmp_path, monkeypatch, caplog):
    from crewbots import browser_process as module

    profile = tmp_path / "browser" / "chrome-live"
    profile.mkdir(parents=True)
    write_owner(profile, 123, "start")
    monkeypatch.setattr(module, "process_info", lambda pid: module.ProcessInfo(456, os.getuid(), "start"))
    profiles = Profiles(profile.parent)
    await profiles.acquire()
    profiles.close()
    assert profile.exists() and "PID 123 PPID 456" in caplog.text


async def test_startup_sweep_stops_only_recorded_orphans_and_removes_confirmed_dead(tmp_path, monkeypatch):
    from crewbots import browser_process as module

    root = tmp_path / "browser"
    root.mkdir()
    orphan, abandoned, unverified = root / "chrome-orphan", root / "chrome-abandoned", root / "chrome-unverified"
    for path in (orphan, abandoned, unverified):
        path.mkdir()
    write_owner(orphan, 101, "start-101")
    write_owner(abandoned, 303, "start-303")
    outside = tmp_path / "personal-profile"
    outside.mkdir()
    (root / "chrome-symlink").symlink_to(outside, target_is_directory=True)
    identities = {101: module.ProcessInfo(1, os.getuid(), "start-101")}
    signals = []

    def info(pid):
        return identities.get(pid)

    def kill(pid, sig):
        signals.append((pid, sig))
        identities.pop(pid)

    monkeypatch.setattr(module, "process_info", info)
    monkeypatch.setattr(module.os, "kill", kill)
    profiles = Profiles(root)
    await profiles.acquire()
    profiles.close()
    assert signals == [(101, signal.SIGTERM)]
    assert not orphan.exists() and not abandoned.exists()
    assert unverified.exists() and outside.is_dir() and (root / "chrome-symlink").is_symlink()


async def test_sweep_leaves_a_live_owner_and_logs_it(tmp_path, monkeypatch, caplog):
    from crewbots import browser_process as module

    root = tmp_path / "browser"
    profile = root / "chrome-live"
    profile.mkdir(parents=True)
    write_owner(profile, 101, "start-101")
    monkeypatch.setattr(module, "process_info", lambda pid: module.ProcessInfo(500, os.getuid(), "start-101"))
    monkeypatch.setattr(module.os, "kill", lambda *args: pytest.fail("must not signal a live owner"))
    profiles = Profiles(root)
    await profiles.acquire()
    profiles.close()
    assert profile.exists() and "verified orphan" in caplog.text


async def test_sweep_never_signals_reused_pid_with_a_different_start_time(tmp_path, monkeypatch, caplog):
    from crewbots import browser_process as module

    root = tmp_path / "browser"
    profile = root / "chrome-old"
    profile.mkdir(parents=True)
    write_owner(profile, 101, "old-start")
    monkeypatch.setattr(module, "process_info", lambda pid: module.ProcessInfo(1, os.getuid(), "new-start"))
    monkeypatch.setattr(module.os, "kill", lambda *args: pytest.fail("must not signal a reused PID"))
    profiles = Profiles(root)
    await profiles.acquire()
    profiles.close()
    assert profile.exists() and "verified orphan" in caplog.text


async def test_sweep_never_infers_ownership_from_url_embedded_flags(tmp_path, monkeypatch, caplog):
    from crewbots import browser_process as module

    root = tmp_path / "browser"
    profile = root / "chrome-not-owned"
    profile.mkdir(parents=True)
    # A user's Chrome may contain these strings inside one URL argument. There is no owner record.
    deceptive_command = f"{CHROME} 'https://example.test/?q=--headless=new --remote-debugging-pipe --user-data-dir={profile} '"
    assert f"--user-data-dir={profile}" in deceptive_command
    monkeypatch.setattr(module, "process_info", lambda pid: pytest.fail("unrecorded processes must never be inspected"))
    monkeypatch.setattr(module.os, "kill", lambda *args: pytest.fail("must not signal unrecorded Chrome"))
    profiles = Profiles(root)
    await profiles.acquire()
    profiles.close()
    assert profile.exists() and "unverified" in caplog.text


async def test_sweep_rechecks_start_time_immediately_before_each_signal(tmp_path, monkeypatch):
    from crewbots import browser_process as module

    root = tmp_path / "browser"
    profile = root / "chrome-old"
    profile.mkdir(parents=True)
    write_owner(profile, 101, "old")
    snapshots = iter([module.ProcessInfo(1, os.getuid(), "old"), module.ProcessInfo(1, os.getuid(), "new"),
                      module.ProcessInfo(1, os.getuid(), "new")])
    monkeypatch.setattr(module, "process_info", lambda pid: next(snapshots))
    monkeypatch.setattr(module.os, "kill", lambda *args: pytest.fail("must not signal a reused PID"))
    profiles = Profiles(root)
    await profiles.acquire()
    profiles.close()
    assert profile.exists()


async def test_unverifiable_identity_and_symlink_owner_are_left_alone(tmp_path, monkeypatch, caplog):
    from crewbots import browser_process as module

    root = tmp_path / "browser"
    restricted, linked = root / "chrome-restricted", root / "chrome-linked"
    restricted.mkdir(parents=True)
    linked.mkdir()
    write_owner(restricted, 101, "start-101")
    secret = tmp_path / "external.json"
    secret.write_text(json.dumps({"pid": 202, "start_time": "x"}))
    (linked / module.OWNER_FILE).symlink_to(secret)

    def unavailable(pid):
        raise BrowserError("identity access denied")

    monkeypatch.setattr(module, "process_info", unavailable)
    monkeypatch.setattr(module.os, "kill", lambda *args: pytest.fail("must not signal an unverified process"))
    profiles = Profiles(root)
    await profiles.acquire()
    profiles.close()
    assert restricted.exists() and linked.exists() and secret.exists()
    assert "identity access denied" in caplog.text and "unverified" in caplog.text


async def test_profile_lock_excludes_another_browser_and_releases(tmp_path):
    root = tmp_path / "browser"
    first, second = Profiles(root), Profiles(root)
    await first.acquire()
    try:
        with pytest.raises(BrowserError, match="Another browser"):
            await second.acquire()
    finally:
        first.close()
    await second.acquire()
    second.close()


async def test_profile_root_cannot_redirect_cleanup_through_a_symlink(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    root = tmp_path / "browser"
    root.symlink_to(other, target_is_directory=True)
    with pytest.raises(BrowserError, match="symlink"):
        await Profiles(root).acquire()
    assert not list(other.iterdir())


async def test_sweep_does_not_wait_on_a_fifo_owner_record(tmp_path, monkeypatch, caplog):
    from crewbots import browser_process as module

    profile = tmp_path / "browser" / "chrome-fifo"
    profile.mkdir(parents=True)
    os.mkfifo(profile / module.OWNER_FILE)
    monkeypatch.setattr(module.os, "kill", lambda *args: pytest.fail("must not signal an unverified process"))
    profiles = Profiles(profile.parent)
    async with asyncio.timeout(1):
        await profiles.acquire()
    profiles.close()
    assert profile.exists() and "invalid browser owner record file" in caplog.text
