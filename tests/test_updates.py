"""Updating crewbots from the app: what's newer in its checkout or on GitHub than what's running, and the release that
brings it in, run apart from the daemon."""

import json
import subprocess
import time

import pytest
from fakes import git_repo

from crewbots import updates
from crewbots.updates import UpdateError, Updates

RELEASE = """#!/bin/bash
# stands in for scripts/release: says it's done, as the real one does at the end
printf '{"state": "done", "step": "Released", "commit": "%s", "pid": %d, "started_at": 1, "at": 2}' \\
  "$(git rev-parse --short=12 HEAD)" $$ > "$HOME/.crewbots/release.json"
echo "from the app: ${CREWBOTS_RELEASE_FROM_APP:-no}, building in ${CARGO_TARGET_DIR:-the checkout}"
"""


def run(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


def commit(path, message):
    (path / "notes.txt").write_text(message)
    run(path, "add", ".")
    run(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", message)
    return run(path, "rev-parse", "--short=12", "HEAD")


@pytest.fixture
def checkout(tmp_path):
    """A checkout of crewbots with a release script, cloned from an upstream (GitHub, here a bare repository)."""
    origin = git_repo(tmp_path / "work")
    (origin / "scripts").mkdir()
    (origin / "scripts" / "release").write_text(RELEASE)
    (origin / "scripts" / "release").chmod(0o755)
    run(origin, "add", ".")
    run(origin, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "Add the release script")
    subprocess.run(["git", "clone", "-q", "--bare", str(origin), str(tmp_path / "upstream.git")], check=True)
    subprocess.run(["git", "clone", "-q", str(tmp_path / "upstream.git"), str(tmp_path / "crewbots")], check=True)
    return tmp_path / "crewbots", origin


def test_what_an_update_brings_and_when_there_is_none(checkout):
    project, _ = checkout
    built = run(project, "rev-parse", "--short=12", "HEAD")
    assert not Updates(project, built).check(built)["available"]

    newer = commit(project, "Show files in chats")
    found = Updates(project, built).check(built)
    assert found["available"] and found["head"] == newer and not found["pull"]
    assert [c["subject"] for c in found["commits"]] == ["Show files in chats"]

    (project / "notes.txt").write_text("half done")  # someone's working on crewbots
    held = Updates(project, built).check(built)
    assert not held["available"] and "changes in progress" in held["note"]

    run(project, "checkout", "-q", "notes.txt")
    behind = Updates(project, newer).check(built)  # the daemon restarted onto it, the app didn't: the app updates
    assert behind["available"] and [c["subject"] for c in behind["commits"]] == ["Show files in chats"]
    (project / "notes.txt").write_text("half done")

    gone = Updates(project, "0123456789ab").check("0123456789ab")  # built from a commit this checkout doesn't have
    assert gone["commits"] == []


def test_an_update_from_github_comes_in_as_a_fast_forward_and_runs_the_release(checkout, tmp_path):
    project, origin = checkout
    built = run(project, "rev-parse", "--short=12", "HEAD")
    pushed = commit(origin, "Pin chats to the top")
    run(origin, "push", "-q", str(tmp_path / "upstream.git"), "HEAD:main")
    found = Updates(project, built).check(built, fetch=True)
    assert found["available"] and found["pull"] and [c["sha"] for c in found["commits"]] == [pushed[:7]]

    started = Updates(project, built).start(built)
    assert run(project, "rev-parse", "--short=12", "HEAD") == pushed  # brought in
    end = time.time() + 5
    while updates.Updates(project, built).release().get("state") != "done" and time.time() < end:
        time.sleep(0.05)
    assert Updates(project, built).release()["commit"] == pushed and started["release"]["state"] in ("running", "done")
    said = updates.log_file().read_text()
    assert "from the app: 1" in said  # it leaves the relaunch to the app
    assert f"building in {updates.build_dir()}" in said  # in a folder of its own

    with pytest.raises(UpdateError, match="up to date"):
        Updates(project, pushed).start(pushed)


def test_a_release_that_stopped_part_way_says_where(checkout):
    project, _ = checkout
    updates.status_file().parent.mkdir(parents=True, exist_ok=True)
    updates.status_file().write_text(json.dumps({"state": "running", "step": "Building the app", "pid": 999999}))
    updates.log_file().parent.mkdir(parents=True, exist_ok=True)
    updates.log_file().write_text("error: linking failed\n")
    release = Updates(project, None).release()
    assert release["state"] == "failed" and release["step"] == "Building the app (it stopped)"
    assert "linking failed" in release["log"]


BUILD = """#!/bin/bash
# stands in for scripts/release started from the app: says the build is ready, as the real one does
printf '{"state": "built", "step": "Built", "commit": "%s", "pid": %d, "started_at": 100, "at": 178, "bundle": "/b"}' \\
  "$(git rev-parse --short=12 HEAD)" $$ > "$HOME/.crewbots/release.json"
echo "background: ${CREWBOTS_RELEASE_BACKGROUND:-no}, expected: ${CREWBOTS_RELEASE_EXPECTED:-?}"
"""


def settled(project, built):
    end = time.time() + 5
    while Updates(project, built).release().get("state") == "running" and time.time() < end:
        time.sleep(0.05)
    return Updates(project, built).release()


def test_an_update_in_the_checkout_is_got_ready_before_anyone_asks(checkout):
    project, _ = checkout
    (project / "scripts" / "release").write_text(BUILD)
    built = run(project, "rev-parse", "--short=12", "HEAD")
    newer = commit(project, "Show files in chats")

    assert Updates(project, built).check(built)["release"] == {}  # only the app's own checks get one ready
    Updates(project, built).check(built, prepare=True)
    ready = settled(project, built)
    assert ready["state"] == "built" and ready["commit"] == newer
    assert "background: 1, expected: 0" in updates.log_file().read_text()  # at low priority, nothing to go by yet

    Updates(project, built).check(built, prepare=True)  # built already: not again
    assert "background: 1" in updates.log_file().read_text() and Updates(project, built).start(built)["release"] == ready

    newest = commit(project, "Pin chats")
    Updates(project, built).check(built, prepare=True)
    assert settled(project, built)["commit"] == newest
    assert "expected: 78" in updates.log_file().read_text()  # how long the last build took, for the app's progress


def test_one_from_github_waits_to_be_asked_and_a_failed_build_isnt_tried_again(checkout, tmp_path):
    project, origin = checkout
    built = run(project, "rev-parse", "--short=12", "HEAD")
    commit(origin, "Pin chats to the top")
    run(origin, "push", "-q", str(tmp_path / "upstream.git"), "HEAD:main")
    assert Updates(project, built).check(built, fetch=True, prepare=True)["release"] == {}  # the checkout stays

    run(project, "merge", "-q", "--ff-only", "@{u}")
    head = run(project, "rev-parse", "--short=12", "HEAD")
    updates.status_file().parent.mkdir(parents=True, exist_ok=True)
    updates.status_file().write_text(json.dumps({"state": "failed", "step": "Checks", "commit": head}))
    assert Updates(project, built).check(built, prepare=True)["release"]["state"] == "failed"
