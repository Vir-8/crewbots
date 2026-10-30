"""Checks handed back with work are rerun by crewbots before its review, once per version: one that fails settles the
round without a reviewer's turn, with its output as proof; when they pass, the reviewers are told."""

import shutil
from pathlib import Path

import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until

from crewbots import worktree
from crewbots.backends import Access
from crewbots.events import Done
from crewbots.runner import checks
from crewbots.store import Status, Store, Verdict


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def plain_shell(monkeypatch):
    """Commands run as they are, where the tests have no Codex sandbox to put them in."""
    ran = []

    def run(command, cwd, network, temp):
        ran.append((command, cwd))
        return ["bash", "-c", command]

    monkeypatch.setattr(checks, "sandboxed", run)
    return ran


def handed_back(store, tmp_path, check_list):
    crew = ScriptedCrew(store, tmp_path, bot("lead", can_delegate=True), bot("engineer", access=Access.WORKSPACE_WRITE),
                        bot("reviewer"), bot("critic"))
    lead = store.create_task("lead", "lead", status=Status.IDLE)
    work = store.create_task("engineer", "build", parent_id=lead.id, created_by="lead", status=Status.IDLE)
    wt = worktree.create(git_repo(tmp_path / "repo"), work.id, root=tmp_path / "wts")
    store.update_task(work.id, repo=str(wt.repo), worktree_path=str(wt.path), branch=wt.branch, base=wt.base, turns=1)
    (wt.path / "hello.py").write_text("print('hi')\n")
    crew.runner.handoff(work.id, "Added hello.py", [], "", "", ["python3 hello.py → hi"], check_list)
    return crew, lead, work


async def test_a_failing_check_fails_the_round_without_a_reviewers_turn(store, tmp_path, plain_shell):
    crew, lead, work = handed_back(store, tmp_path, ["python3 hello.py", "test -f missing.txt"])
    reviews = [crew.runner.delegate(lead.id, name, "review it", review_of=work.id) for name in ("reviewer", "critic")]
    for review in reviews:
        await wait_until(crew.settled(review.id))

    assert not crew.specs_for("reviewer") and not crew.specs_for("critic")
    assert len(plain_shell) == 2  # once for the round, not once per reviewer
    state = crew.runner.review_state(work.id)
    assert state.failed and state.round == 1
    [finding] = store.latest_review(reviews[0].id).findings
    assert finding["problem"] == "`test -f missing.txt` exits 1 in a clean copy of the work"
    assert finding["proof"].startswith("$ test -f missing.txt")
    assert "1 of 2 failed" in store.get_task(reviews[1].id).result
    await crew.runner.shutdown()


async def test_passing_checks_run_in_the_reviews_copy_and_the_reviewer_is_told(store, tmp_path, plain_shell):
    crew, lead, work = handed_back(store, tmp_path, ["python3 hello.py"])
    crew.script["reviewer"] = lambda spec, me: (
        crew.runner.review(me.id, Verdict.PASS, "fine", []), [Done(final_text="ok")])[1]
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))

    [(_, cwd)] = plain_shell
    assert cwd == worktree_path(store, review.id) and cwd != worktree_path(store, work.id)  # the copy, not the work
    [spec] = crew.specs_for("reviewer")
    assert "they all passed:\n- `python3 hello.py`: passed in" in spec.prompt
    assert crew.runner.review_state(work.id).approved
    [rerun] = [e for e in store.events(work.id) if e.kind == "checks_rerun"]
    assert rerun.data["version"] == 1
    await crew.runner.shutdown()


async def test_a_review_of_a_superseded_version_skips_the_newer_checks(store, tmp_path, plain_shell):
    crew, lead, work = handed_back(store, tmp_path, ["python3 hello.py"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    # handed back again before the reviewer's first turn: its checks belong to a version this copy doesn't have
    crew.runner.handoff(work.id, "Removed hello.py", [], "", "", ["ls → no hello.py"], ["test ! -f hello.py"])
    store.update_task(work.id, turns=2)
    await wait_until(crew.settled(review.id))

    assert not plain_shell
    [spec] = crew.specs_for("reviewer")
    assert "crewbots reran" not in spec.prompt
    await crew.runner.shutdown()


async def test_without_a_sandbox_the_reviewers_run_them_as_before(store, tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "sandboxed", lambda *_: None)
    crew, lead, work = handed_back(store, tmp_path, ["false"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))
    [spec] = crew.specs_for("reviewer")
    assert "crewbots reran" not in spec.prompt
    await crew.runner.shutdown()


@pytest.mark.skipif(shutil.which("codex") is None, reason="needs the Codex CLI's sandbox")
async def test_a_check_runs_in_a_writing_bots_sandbox(store, tmp_path):
    outside = tmp_path / "outside.txt"
    crew, lead, work = handed_back(store, tmp_path, [f"touch inside.txt && touch {outside}"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id), timeout=60)

    assert not outside.exists()
    [finding] = store.latest_review(review.id).findings
    assert "Operation not permitted" in finding["proof"]
    await crew.runner.shutdown()


def worktree_path(store, task_id):
    task = store.get_task(task_id)
    return Path(task.workspace if task.review_of else task.worktree_path)
