"""Push proposals require the user's API and pin the work shown on the card."""

import asyncio
import atexit
import functools
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fakes import bot, git_repo, make_crew
from fastapi.testclient import TestClient

import crewbots.push as push_module
from crewbots.api import create_app
from crewbots.projects import Project
from crewbots.push import _git
from crewbots.push import propose as async_propose
from crewbots.runner import NotAllowed, Runner
from crewbots.store import Store


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout.strip()


@functools.cache
def _pushed(files: tuple[tuple[str, str], ...]) -> Path:
    """A bare remote and a repo whose main is pushed to it, with a commit on top for each (file, text), made once:
    pushed_repo copies it, where building it takes a third of a second."""
    path = Path(tempfile.mkdtemp(prefix="crewbots-tests-pushed-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    remote = path / "remote.git"
    git(path, "init", "--bare", "-q", str(remote))
    git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    repo = git_repo(path / "repo")
    git(repo, "remote", "add", "origin", "../remote.git")
    git(repo, "push", "-q", "-u", "origin", "main")
    for name, text in files:
        (repo / name).write_text(text)
        git(repo, "add", name)
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", f"Add {text}")
    return path


def pushed_repo(tmp_path: Path, *files: tuple[str, str]) -> tuple[Path, Path]:
    """The repo and its bare remote in `tmp_path`, with main pushed and then a commit for each (file, text)."""
    shutil.copytree(_pushed(files), tmp_path, dirs_exist_ok=True, symlinks=True, copy_function=shutil.copy)
    repo, remote = tmp_path / "repo", tmp_path / "remote.git"
    git(repo, "remote", "set-url", "origin", str(remote))
    return repo, remote


def chief_runner(tmp_path: Path) -> Runner:
    """A runner with a chief and a "demo" project, for tests that never reach git."""
    crew = make_crew(tmp_path, bot("chief", can_delegate=True))
    crew.projects["demo"] = Project("demo", tmp_path, "", tmp_path / "demo.md")
    return Runner(Store(":memory:"), crew)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    repo, remote = pushed_repo(tmp_path, ("new.txt", "one"))
    monkeypatch.setattr(push_module.ci, "check", lambda *a: {"status": "none"})  # test_ci runs the real one
    store = Store(":memory:")
    crew = make_crew(tmp_path, bot("chief", can_delegate=True), bot("engineer"))
    crew.projects["demo"] = Project("demo", repo, "", tmp_path / "demo.md", ci=("true",))
    runner = Runner(store, crew)
    notices = []
    monkeypatch.setattr(runner, "send", lambda *a, **kw: notices.append((a, kw)))
    with TestClient(create_app(runner, "admin"), base_url="http://127.0.0.1:8765/v1") as client:
        yield runner, client, repo, remote, notices
    store.close()


def propose(*args):
    return asyncio.run(async_propose(*args))


def press(client, proposal):
    return client.post(f"/push-proposals/{proposal}/push", headers={"Authorization": "Bearer admin"}).json()


def test_push_requires_button_and_matches_commits(setup):
    runner, client, repo, remote, notices = setup
    chat = runner.chat("chief")
    before = git(remote, "rev-parse", "refs/heads/main")
    runner.store.set_auto_merge(True)
    proposal = propose(runner, chat.id, "demo", "Add one")
    event = runner.store.last_event(chat.id, ("push_proposed",))
    assert event.data["commits"] == git(repo, "log", "--format=%H %s", "@{u}..HEAD").splitlines()
    assert git(remote, "rev-parse", "refs/heads/main") == before
    assert press(client, proposal)["ok"]
    assert git(remote, "rev-parse", "refs/heads/main") == git(repo, "rev-parse", "HEAD")
    assert notices and "completed" in notices[0][0][1]
    assert client.post(f"/push-proposals/{proposal}/push", headers={"Authorization": "Bearer admin"}).status_code == 403


def test_stale_card_refuses_new_commit(setup):
    runner, client, repo, remote, _ = setup
    proposal = propose(runner, runner.chat("chief").id, "demo", "Add one")
    before = git(remote, "rev-parse", "refs/heads/main")
    (repo / "new.txt").write_text("two")
    git(repo, "add", "new.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Add two")
    result = press(client, proposal)
    assert not result["ok"] and "changed" in result["reason"]
    assert git(remote, "rev-parse", "refs/heads/main") == before


def test_a_commit_with_an_ai_trailer_is_not_pushed(setup):
    runner, client, repo, remote, _ = setup
    (repo / "two.txt").write_text("two")
    git(repo, "add", "two.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
        "Add two\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>")
    before = git(remote, "rev-parse", "refs/heads/main")
    proposal = propose(runner, runner.chat("chief").id, "demo", "Add both")
    error = runner.store.last_event(runner.chat("chief").id, ("push_proposed",)).data["error"]
    assert "AI co-author trailer" in error and "Add two" in error and "Add one" not in error
    assert not press(client, proposal)["ok"]
    assert git(remote, "rev-parse", "refs/heads/main") == before


def test_no_upstream_shows_git_reason(setup):
    runner, client, repo, _, _ = setup
    git(repo, "branch", "--unset-upstream")
    proposal = propose(runner, runner.chat("chief").id, "demo", "Add one")
    event = runner.store.last_event(runner.chat("chief").id, ("push_proposed",))
    assert "upstream" in event.data["error"].lower()
    assert "upstream" in press(client, proposal)["reason"].lower()


def test_chief_hears_why_a_proposal_cannot_be_made(setup):
    runner, client, repo, _, _ = setup
    git(repo, "branch", "--unset-upstream")
    runner._tokens["chief-token"] = runner.chat("chief").id
    response = client.post("/bot/propose_push", headers={"Authorization": "Bearer chief-token"},
                           json={"project": "demo", "summary": "Ready"})
    assert response.status_code == 200
    assert "upstream" in response.json()["message"].lower()


def test_non_fast_forward_reports_reason(setup, tmp_path):
    runner, client, repo, remote, _ = setup
    proposal = propose(runner, runner.chat("chief").id, "demo", "Add one")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(other)], check=True)
    (other / "other.txt").write_text("other")
    git(other, "add", "other.txt")
    git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Other")
    git(other, "push", "origin", "main")
    before = git(remote, "rev-parse", "refs/heads/main")
    git(repo, "fetch", "origin", "main")  # the remote commit is known locally, but diverges
    result = press(client, proposal)
    assert not result["ok"] and "not a fast-forward" in result["reason"].lower()
    assert git(remote, "rev-parse", "refs/heads/main") == before


def test_unknown_remote_tip_refuses_at_proposal(setup, tmp_path):
    runner, _, _, remote, _ = setup
    other = tmp_path / "unknown"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(other)], check=True)
    (other / "new.txt").write_text("remote change")
    git(other, "add", "new.txt")
    git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Remote change")
    git(other, "push", "origin", "main")
    chat = runner.chat("chief")
    propose(runner, chat.id, "demo", "Ready")
    event = runner.store.last_event(chat.id, ("push_proposed",))
    assert "not a fast-forward" in event.data["error"]
    assert (event.data["branch"], event.data["upstream"]) == ("main", "origin/main")
    assert event.data["push_url_display"] == str(remote)


def test_bot_api_proposes_but_bot_token_cannot_push(setup):
    runner, client, _, remote, _ = setup
    chief = runner.chat("chief")
    runner._tokens["chief-token"] = chief.id
    headers = {"Authorization": "Bearer chief-token"}
    before = git(remote, "rev-parse", "refs/heads/main")
    response = client.post("/bot/propose_push", headers=headers, json={"project": "demo", "summary": "Ready"})
    assert response.status_code == 200
    assert client.post(f"/push-proposals/{response.json()['id']}/push", headers=headers).status_code == 403
    assert git(remote, "rev-parse", "refs/heads/main") == before


def test_git_timeout_kills_the_process_group(tmp_path):
    repo = git_repo(tmp_path / "repo")
    started = time.monotonic()
    with pytest.raises(NotAllowed, match="timed out") as read_timeout:
        _git(repo, "-c", "alias.wait=!sleep 5", "wait", timeout=0.2)
    # Were only git killed, the orphaned sleep would hold the pipes for the 2 s _git then waits on them.
    assert time.monotonic() - started < 1.5
    assert "push may have completed" not in str(read_timeout.value)
    with pytest.raises(NotAllowed, match="push may have completed"):
        _git(repo, "-c", "alias.wait=!sleep 5", "wait", timeout=0.2, pushing=True)


def test_second_push_uses_real_push_remote_tip_with_different_fetch_url(setup, tmp_path):
    runner, client, repo, fetch_remote, _ = setup
    push_remote = tmp_path / "push.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(fetch_remote), str(push_remote)], check=True)
    git(repo, "remote", "set-url", "--push", "origin", str(push_remote))
    initial = git(push_remote, "rev-parse", "refs/heads/main")
    chat = runner.chat("chief")
    first = propose(runner, chat.id, "demo", "First")
    assert press(client, first)["pushed"]
    first_head = git(repo, "rev-parse", "HEAD")
    assert git(repo, "rev-parse", "refs/remotes/origin/main") == first_head
    assert git(fetch_remote, "rev-parse", "refs/heads/main") == initial
    (repo / "new.txt").write_text("second")
    git(repo, "add", "new.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Add two")
    git(repo, "update-ref", "refs/remotes/origin/main", initial)  # simulate stale fetch tracking
    second = propose(runner, chat.id, "demo", "Second")
    data = runner.store.last_event(chat.id, ("push_proposed",)).data
    assert data["commits"] == git(repo, "log", "--format=%H %s", f"{first_head}..HEAD").splitlines()
    assert len(data["commits"]) == 1
    assert press(client, second)["pushed"]
    assert git(push_remote, "rev-parse", "refs/heads/main") == git(repo, "rev-parse", "HEAD")


def test_up_to_date_reports_nothing_pushed(setup):
    runner, client, repo, remote, notices = setup
    first = propose(runner, runner.chat("chief").id, "demo", "First")
    assert press(client, first)["pushed"]
    second = propose(runner, runner.chat("chief").id, "demo", "Already up to date")
    before = git(remote, "rev-parse", "refs/heads/main")
    result = press(client, second)
    assert result["ok"] and not result["pushed"] and "Nothing was pushed" in result["reason"]
    assert "Nothing was pushed" in notices[-1][0][1]
    assert git(remote, "rev-parse", "refs/heads/main") == before == git(repo, "rev-parse", "HEAD")


def test_pre_push_hook_never_runs(setup):
    runner, client, repo, _, _ = setup
    marker = repo / "hook-ran"
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    hook.chmod(0o755)
    reference_hook = hook.parent / "reference-transaction"
    reference_hook.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n")
    reference_hook.chmod(0o755)
    assert press(client, propose(runner, runner.chat("chief").id, "demo", "Ready"))["pushed"]
    assert not marker.exists()


def test_missing_tracking_ref_is_not_made_by_crewbots(setup, monkeypatch):
    runner, client, repo, remote, _ = setup
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    ran = []
    monkeypatch.setattr(push_module, "_git", lambda folder, *args, **kw: (ran.append(args), _git(folder, *args, **kw))[1])
    proposal = propose(runner, runner.chat("chief").id, "demo", "Ready")
    assert press(client, proposal)["pushed"]
    head = git(repo, "rev-parse", "HEAD")
    assert git(remote, "rev-parse", "refs/heads/main") == head
    assert not any("update-ref" in args for args in ran)
    # A newer git (2.56) makes it in the push itself, at what was pushed; an older one leaves it missing.
    made = subprocess.run(["git", "-C", str(repo), "show-ref", "--verify", "--quiet", "refs/remotes/origin/main"])
    assert made.returncode == 1 or git(repo, "rev-parse", "refs/remotes/origin/main") == head


def test_a_tracking_ref_the_push_moved_itself_is_no_warning(setup, monkeypatch):
    runner, client, repo, remote, _ = setup

    def newer_git(folder, *args, **kw):  # as git 2.56 does, the push moves the tracking ref too
        out = _git(folder, *args, **kw)
        if "push" in args:
            _git(folder, "update-ref", "refs/remotes/origin/main", "HEAD")
        return out

    monkeypatch.setattr(push_module, "_git", newer_git)
    pushed = press(client, propose(runner, runner.chat("chief").id, "demo", "Ready"))
    assert pushed["pushed"] and pushed["reason"] == "Push completed."
    assert git(repo, "rev-parse", "refs/remotes/origin/main") == git(repo, "rev-parse", "HEAD")


def test_started_without_result_cannot_be_pressed_again(setup):
    runner, client, _, remote, _ = setup
    chat = runner.chat("chief")
    proposal = propose(runner, chat.id, "demo", "Ready")
    runner._record(chat.id, kind="push_started", data={"proposal": proposal})
    before = git(remote, "rev-parse", "refs/heads/main")
    response = client.post(f"/push-proposals/{proposal}/push", headers={"Authorization": "Bearer admin"})
    assert response.status_code == 403 and "check the remote" in response.json()["detail"]
    assert git(remote, "rev-parse", "refs/heads/main") == before


def test_failure_redacts_password_with_slashes_and_at_signs(tmp_path):
    repo = git_repo(tmp_path / "repo")
    secret = "secret/with@several@parts"
    with pytest.raises(NotAllowed) as raised:
        _git(repo, "-c", f"alias.fail=!printf '%s\\n' 'custom://user:{secret}@example.invalid/repo' >&2; exit 1", "fail")
    assert secret not in str(raised.value)
    assert "secret" not in str(raised.value)
    assert "custom://***@example.invalid/repo" in str(raised.value)


@pytest.mark.asyncio
async def test_proposal_remote_snapshot_does_not_block_the_event_loop(tmp_path, monkeypatch):
    runner = chief_runner(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    resumed = asyncio.Event()

    def slow_snapshot(_folder):
        entered.set()
        loop.call_soon_threadsafe(resumed.set)
        release.wait(2)
        return {"commits": [], "head": "a" * 40, "destination": "refs/heads/main"}

    monkeypatch.setattr("crewbots.push.snapshot", slow_snapshot)
    monkeypatch.setattr("crewbots.push.ci.check", lambda *a: {"status": "none"})
    job = asyncio.create_task(async_propose(runner, runner.chat("chief").id, "demo", "Ready"))
    try:
        await asyncio.wait_for(resumed.wait(), 1)
        assert entered.is_set() and not job.done()
    finally:
        release.set()
        await job
        runner.store.close()


@pytest.mark.parametrize("change", ["advance", "delete", "advance_to_head"])
def test_remote_change_after_snapshot_is_refused(setup, monkeypatch, tmp_path, change):
    runner, client, repo, remote, _ = setup
    other = tmp_path / "other-race"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(other)], check=True)
    (other / "race.txt").write_text("race")
    git(other, "add", "race.txt")
    git(other, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Race")
    proposal = propose(runner, runner.chat("chief").id, "demo", "Ready")
    original = push_module._git
    changed = None

    def race(folder, *args, **kwargs):
        nonlocal changed
        if "push" in args and kwargs.get("pushing"):
            if change == "delete":
                git(remote, "update-ref", "-d", "refs/heads/main")
                changed = "deleted"
            elif change == "advance_to_head":
                git(repo, "push", "origin", "main")
                changed = git(remote, "rev-parse", "refs/heads/main")
            else:
                git(other, "push", "origin", "main")
                changed = git(remote, "rev-parse", "refs/heads/main")
        return original(folder, *args, **kwargs)

    monkeypatch.setattr(push_module, "_git", race)
    result = press(client, proposal)
    assert not result["ok"] and "The remote changed; make a new proposal" in result["reason"]
    if change == "delete":
        assert subprocess.run(["git", "-C", str(remote), "show-ref", "--verify", "--quiet", "refs/heads/main"]).returncode == 1
    else:
        assert git(remote, "rev-parse", "refs/heads/main") == changed
    assert runner.store.last_event(runner.chat("chief").id, ("push_result",)).data == result


def test_ls_remote_selects_exact_ref(setup):
    runner, client, repo, remote, _ = setup
    side = git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit-tree", "HEAD^{tree}", "-m", "Side")
    # ls-remote lists this one too, and first: it also ends in refs/heads/main
    git(repo, "push", "-q", "origin", f"{side}:refs/heads/a/refs/heads/main")
    chat = runner.chat("chief")
    proposal = propose(runner, chat.id, "demo", "Ready")
    assert runner.store.last_event(chat.id, ("push_proposed",)).data["tip"] == git(remote, "rev-parse", "refs/heads/main")
    assert press(client, proposal)["pushed"]
    assert git(remote, "rev-parse", "refs/heads/main") == git(repo, "rev-parse", "HEAD")


def test_non_utf8_subject_and_unexpected_press_error_record_result(setup, monkeypatch):
    runner, client, repo, _, _ = setup
    head = git(repo, "rev-parse", "HEAD")
    raw = subprocess.run(["git", "-C", str(repo), "cat-file", "commit", head], capture_output=True, check=True).stdout
    raw = raw.split(b"\n\n", 1)[0] + b"\n\nInvalid \xff subject\n"
    commit = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
                            input=raw, capture_output=True, check=True).stdout.decode().strip()
    git(repo, "update-ref", "refs/heads/main", commit)
    chat = runner.chat("chief")
    proposal = propose(runner, chat.id, "demo", "Ready")
    assert "\ufffd" in runner.store.last_event(chat.id, ("push_proposed",)).data["commits"][0]
    assert press(client, proposal)["pushed"]
    proposal = propose(runner, chat.id, "demo", "Again")
    monkeypatch.setattr(push_module, "_attempt", lambda *args: (_ for _ in ()).throw(RuntimeError("unexpected failure")))
    result = press(client, proposal)
    assert not result["ok"] and result["reason"] == "unexpected failure"
    assert runner.store.last_event(chat.id, ("push_result",)).data == result


@pytest.mark.asyncio
async def test_a_project_has_one_push_card_at_a_time(tmp_path, monkeypatch):
    runner = chief_runner(tmp_path)
    store = runner.store
    head = {"value": "a" * 40}
    monkeypatch.setattr(push_module, "snapshot", lambda folder: {
        "branch": "main", "destination": "refs/heads/main", "url_hash": "h", "tip": "0" * 40, "head": head["value"],
        "commits": [f"{head['value'][:7]} Fix"]})
    checks = []
    monkeypatch.setattr(push_module.ci, "check", lambda *a: checks.append(a[1]) or {"status": "passed"})
    chat = runner.chat("chief")

    first, again = await asyncio.gather(async_propose(runner, chat.id, "demo", "Ready"),
                                        async_propose(runner, chat.id, "demo", "Ready, again"))
    assert first == again and len(checks) == 1  # two at once: one card, one CI run
    assert len(store.last_events(chat.id, ("push_proposed",), 5)) == 1

    head["value"] = "b" * 40  # more commits since: the newer card replaces the open one
    newer = await async_propose(runner, chat.id, "demo", "Ready, with the fix")
    assert newer != first and push_module.open_card(runner, chat.id, "demo")[0] == newer
    [replaced] = store.last_events(chat.id, ("push_replaced",), 5)
    assert replaced.data == {"proposal": first, "by": newer}
    with pytest.raises(NotAllowed, match="newer Push card replaced"):
        await push_module.perform(runner, first)
    store.close()


@pytest.mark.parametrize(("url", "expected"), [
    ("https://user:secret@example.com/team/repo.git?token=secret#secret", "example.com/team/repo.git"),
    ("ssh://git:secret@example.com:2222/team/repo", "example.com:2222/team/repo"),
    ("git@example.com:team/repo.git", "example.com:team/repo.git"),
    ("/tmp/repo.git", "/tmp/repo.git"),
])
def test_destination_display_hides_credentials(url, expected):
    assert push_module._display_url(url) == expected
