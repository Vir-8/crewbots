"""Push proposals require the user's API and pin the work shown on the card."""

import asyncio
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from fakes import bot, git_repo, make_crew
from fastapi.testclient import TestClient

import crewbots.push as push_module
from crewbots.api import create_app
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.projects import Project
from crewbots.push import _git
from crewbots.push import propose as async_propose
from crewbots.runner import NotAllowed, Runner
from crewbots.store import Store


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = git_repo(tmp_path / "repo")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    (repo / "new.txt").write_text("one")
    git(repo, "add", "new.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "Add one")
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


def test_worker_cannot_propose(setup):
    runner, _, _, _, _ = setup
    with pytest.raises(NotAllowed):
        propose(runner, runner.chat("engineer").id, "demo", "work")


def test_selected_chief_can_propose_without_legacy_delegate_flag(setup, monkeypatch):
    runner, _, _, _, _ = setup
    chief = replace(runner.crew.bots["chief"], can_delegate=False)
    monkeypatch.setattr(runner, "coordinator", lambda: chief)
    chat = runner.chat("chief")
    proposal = propose(runner, chat.id, "demo", "Ready")
    assert runner.store.last_event(chat.id, ("push_proposed",)).id == proposal


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


def test_git_timeout_kills_the_process_group(setup):
    _, _, repo, _, _ = setup
    started = time.monotonic()
    with pytest.raises(NotAllowed, match="timed out") as read_timeout:
        _git(repo, "-c", "alias.wait=!sleep 5", "wait", timeout=1)
    assert time.monotonic() - started < 4
    assert "push may have completed" not in str(read_timeout.value)
    with pytest.raises(NotAllowed, match="push may have completed"):
        _git(repo, "-c", "alias.wait=!sleep 5", "wait", timeout=1, pushing=True)


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


def test_non_chief_coordinator_is_refused(setup):
    runner, client, _, _, _ = setup
    runner.crew.bots["lead"] = bot("lead", can_delegate=True)
    task = runner.chat("lead")
    runner._tokens["lead-token"] = task.id
    response = client.post("/bot/propose_push", headers={"Authorization": "Bearer lead-token"},
                           json={"project": "demo", "summary": "work"})
    assert response.status_code == 403
    assert "chief" in response.json()["detail"]


@pytest.mark.asyncio
async def test_in_process_tool_is_only_exposed_to_chief(setup):
    runner, _, _, _, _ = setup
    runner.crew.bots["lead"] = bot("lead", can_delegate=True)
    chief = CrewTools(runner, runner.chat("chief").id, True)
    lead = CrewTools(runner, runner.chat("lead").id, True)
    assert "propose_push" in {tool.name for tool in chief.definitions()}
    assert "propose_push" not in {tool.name for tool in lead.definitions()}
    with pytest.raises(ToolRefused, match="chief"):
        await lead.call("propose_push", {"project": "demo", "summary": "Ready"})


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


def test_failure_redacts_password_with_slashes_and_at_signs(setup):
    _, _, repo, _, _ = setup
    secret = "secret/with@several@parts"
    with pytest.raises(NotAllowed) as raised:
        _git(repo, "-c", f"alias.fail=!printf '%s\\n' 'custom://user:{secret}@example.invalid/repo' >&2; exit 1", "fail")
    assert secret not in str(raised.value)
    assert "secret" not in str(raised.value)
    assert "custom://***@example.invalid/repo" in str(raised.value)


@pytest.mark.asyncio
async def test_proposal_remote_snapshot_does_not_block_the_event_loop(setup, monkeypatch):
    runner, _, _, _, _ = setup
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
    runner, client, _, remote, _ = setup
    git(remote, "update-ref", "refs/heads/decoy/main", git(remote, "rev-parse", "refs/heads/main"))
    proposal = propose(runner, runner.chat("chief").id, "demo", "Ready")
    assert press(client, proposal)["pushed"]


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


@pytest.mark.parametrize(("url", "expected"), [
    ("https://user:secret@example.com/team/repo.git?token=secret#secret", "example.com/team/repo.git"),
    ("ssh://git:secret@example.com:2222/team/repo", "example.com:2222/team/repo"),
    ("git@example.com:team/repo.git", "example.com:team/repo.git"),
    ("/tmp/repo.git", "/tmp/repo.git"),
])
def test_destination_display_hides_credentials(url, expected):
    assert push_module._display_url(url) == expected
