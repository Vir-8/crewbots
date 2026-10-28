"""The review loop and the merge: verdicts, rounds counted in code, the stop condition, and merging approved work."""

import subprocess
from pathlib import Path

import httpx
import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until

from crewbots import paths, worktree
from crewbots.api import create_app
from crewbots.backends import Access
from crewbots.events import Done
from crewbots.runner import MAX_REVIEW_ROUNDS, MergeError, NotAllowed
from crewbots.store import Status, Store, Verdict


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def crew_for(store, tmp_path):
    return ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True),
                        bot("engineer", access=Access.WORKSPACE_WRITE), bot("reviewer"))


def finished_work(store, crew, turns=1):
    """A chief task, and an engineer task it delegated that has finished `turns` runs."""
    parent = store.create_task("chief", "lead")
    store.update_task(parent.id, status=Status.IDLE)
    work = store.create_task("engineer", "build", parent_id=parent.id, created_by="chief")
    store.update_task(work.id, status=Status.IDLE, turns=turns)
    return parent, work


async def review_round(crew, parent, work, verdict):
    review = crew.runner.delegate(parent.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))
    crew.runner.review(review.id, verdict, f"{verdict} it", [{"location": "a.py:1", "problem": "x"}])
    return review


@pytest.mark.parametrize("coordinator", ["chief", "eng-lead"])
async def test_coordinators_propose_only_reviewed_work_and_withdraw(store, tmp_path, coordinator):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True), bot("eng-lead", can_delegate=True),
                        bot("engineer"), bot("reviewer"))
    parent = store.create_task(coordinator, "lead", status=Status.IDLE)
    work = store.create_task("engineer", "build", parent_id=parent.id, created_by=coordinator)
    (tmp_path / "wt").mkdir()
    store.update_task(work.id, status=Status.IDLE, turns=1, worktree_path=str(tmp_path / "wt"))
    with pytest.raises(NotAllowed, match="not approved"):
        crew.runner.propose_merge(parent.id, work.id, "Ready")
    await review_round(crew, parent, work, Verdict.PASS)
    assert crew.runner.propose_merge(parent.id, work.id, "Ready").merge_state == "proposed"
    assert crew.runner.withdraw_merge(work.id, "Revising", parent_id=parent.id).merge_state is None
    assert crew.runner.next_check(parent.id, 10, "follow up")


def git(repo, *args):
    return subprocess.run([paths.git(), *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


async def test_a_verdict_is_recorded_on_the_review_and_the_work(store, tmp_path):
    crew = crew_for(store, tmp_path)
    parent, work = finished_work(store, crew)

    review = await review_round(crew, parent, work, Verdict.PASS)

    assert store.latest_review(review.id).verdict is Verdict.PASS
    assert "review" in [e.kind for e in store.events(work.id)]
    assert crew.runner.review_state(work.id).approved


async def test_only_a_review_task_gives_a_verdict(store, tmp_path):
    crew = crew_for(store, tmp_path)
    _, work = finished_work(store, crew)

    with pytest.raises(NotAllowed, match="not a review"):
        crew.runner.review(work.id, Verdict.PASS, "me, reviewing myself", [])


async def test_running_work_and_reviews_themselves_cannot_be_reviewed(store, tmp_path):
    crew = crew_for(store, tmp_path)
    parent, work = finished_work(store, crew)
    review = await review_round(crew, parent, work, Verdict.PASS)

    with pytest.raises(NotAllowed, match="is itself a review"):
        crew.runner.delegate(parent.id, "reviewer", "meta", review_of=review.id)
    store.update_task(work.id, status=Status.RUNNING)
    with pytest.raises(NotAllowed, match="still working"):
        crew.runner.delegate(parent.id, "reviewer", "again", review_of=work.id)


async def test_a_round_is_a_reviewed_version_of_the_work(store, tmp_path):
    crew = crew_for(store, tmp_path)
    parent, work = finished_work(store, crew)

    await review_round(crew, parent, work, Verdict.FAIL)
    await review_round(crew, parent, work, Verdict.PASS)  # a second reviewer of the same version: still round 1
    assert crew.runner.review_state(work.id).round == 1
    assert not crew.runner.review_state(work.id).approved  # one of the round's reviews failed

    store.update_task(work.id, turns=2)  # the engineer revised it
    await review_round(crew, parent, work, Verdict.PASS)

    state = crew.runner.review_state(work.id)
    assert (state.round, state.rounds, state.approved) == (2, [1, 2], True)


async def test_an_old_approval_does_not_cover_newer_changes(store, tmp_path):
    crew = crew_for(store, tmp_path)
    parent, work = finished_work(store, crew)
    await review_round(crew, parent, work, Verdict.PASS)

    store.update_task(work.id, turns=2, worktree_path=str(tmp_path / "wt"))  # changed after it was approved

    assert not crew.runner.review_state(work.id).approved
    with pytest.raises(NotAllowed, match="not approved"):
        crew.runner.propose_merge(parent.id, work.id, "ship it")


async def test_after_three_failed_rounds_there_is_no_fourth(store, tmp_path):
    crew = crew_for(store, tmp_path)
    parent, work = finished_work(store, crew)
    for n in range(1, MAX_REVIEW_ROUNDS + 1):
        store.update_task(work.id, turns=n)
        await review_round(crew, parent, work, Verdict.FAIL)

    state = crew.runner.review_state(work.id)
    assert state.out_of_rounds
    with pytest.raises(NotAllowed, match="stop condition"):
        crew.runner.message_child(parent.id, work.id, "try once more")
    store.update_task(work.id, turns=MAX_REVIEW_ROUNDS + 1)
    with pytest.raises(NotAllowed, match="stop condition"):
        crew.runner.delegate(parent.id, "reviewer", "round four", review_of=work.id)


async def test_an_escalation_can_still_be_answered_after_the_last_round(store, tmp_path):
    crew = crew_for(store, tmp_path)
    parent, work = finished_work(store, crew)
    for n in range(1, MAX_REVIEW_ROUNDS + 1):
        store.update_task(work.id, turns=n)
        await review_round(crew, parent, work, Verdict.FAIL)
    store.update_task(work.id, status=Status.NEEDS_INPUT)

    crew.runner.message_child(parent.id, work.id, "here is the decision")  # not refused


async def test_the_chief_hears_each_verdict_and_what_to_do_next(store, tmp_path):
    crew = crew_for(store, tmp_path)
    reviews = []

    def chief(spec, me):
        if spec.prompt == "go":
            crew.runner.delegate(me.id, "engineer", "build")
        elif "engineer" in spec.prompt and not reviews:
            work = crew.store.children(me.id)[0]
            reviews.append(crew.runner.delegate(me.id, "reviewer", "review", review_of=work.id))
        return [Done(final_text="ok")]

    def reviewer(spec, me):
        crew.runner.review(me.id, Verdict.FAIL, "off by one",
                           [{"location": "calc.py:3", "problem": "returns a - b + 1", "scenario": "f(2, 1) gives 2"}])
        return [Done(final_text="failed it")]

    crew.script = {"chief": chief, "reviewer": reviewer}
    top = crew.runner.submit("chief", "go")
    await wait_until(lambda: bool(reviews) and crew.settled(reviews[0].id)() and crew.settled(top.id)())

    report = [e.data["text"] for e in store.events(top.id) if e.kind == "message"][-1]
    assert "Review of t_" in report and "FAIL" in report
    assert "calc.py:3: returns a - b + 1 Scenario: f(2, 1) gives 2" in report
    assert f"round 1 of {MAX_REVIEW_ROUNDS}: changes requested" in report
    assert "This task is review round 1 of 3" in crew.specs_for("reviewer")[0].system_prompt


async def test_approved_work_is_proposed_and_the_user_merges_it(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = crew_for(store, tmp_path)
    state = {}

    def chief(spec, me):
        if spec.prompt == "go":
            state["work"] = crew.runner.delegate(me.id, "engineer", "add hello.py")
        elif "review" not in state:
            state["review"] = crew.runner.delegate(me.id, "reviewer", "review", review_of=state["work"].id)
        elif "approved" in spec.prompt and "proposed" not in state:
            state["proposed"] = crew.runner.propose_merge(me.id, state["work"].id, "adds hello.py; reviewed")
        return [Done(final_text="ok")]

    def engineer(spec, me):
        (spec.cwd / "hello.py").write_text("def hello():\n    return 'hi'\n")
        return [Done(final_text="wrote hello.py")]

    def reviewer(spec, me):
        assert (spec.cwd / "hello.py").exists()  # the reviewer is looking at the engineer's worktree
        crew.runner.review(me.id, Verdict.PASS, "looks right", [])
        return [Done(final_text="passed")]

    crew.script = {"chief": chief, "engineer": engineer, "reviewer": reviewer}
    top = crew.runner.submit("chief", "go", workspace=str(repo))
    await wait_until(lambda: "proposed" in state and crew.settled(top.id)())
    work = store.get_task(state["work"].id)
    assert work.merge_state == "proposed"
    assert not (repo / "hello.py").exists()  # nothing lands until the user merges

    merged = crew.runner.merge(work.id)

    assert merged.merge_state == "merged"
    assert (repo / "hello.py").read_text().startswith("def hello")
    assert git(repo, "rev-parse", "HEAD") == merged.merged_commit
    message = git(repo, "log", "-1", "--format=%B")
    assert message.startswith("adds hello.py; reviewed\n") and "reviewer (pass)" in message
    assert not tmp_path.joinpath(work.worktree_path).exists()
    assert work.branch not in git(repo, "branch")
    with pytest.raises(MergeError, match="already merged"):
        crew.runner.merge(work.id)


@pytest.mark.parametrize("enabled,approved,conflict", [
    (False, True, False), (True, True, False), (True, False, False), (True, True, True),
])
async def test_automatic_merge_is_opt_in_and_preserves_failed_proposals(store, tmp_path, monkeypatch,
                                                                           enabled, approved, conflict):
    repo = git_repo(tmp_path / "repo")
    crew = crew_for(store, tmp_path)
    parent = store.create_task("chief", "lead", workspace=str(repo), status=Status.IDLE)
    crew.script["engineer"] = lambda spec, me: (
        (spec.cwd / "README.md").write_text("bot edit\n"), [Done(final_text="edited")])[1]
    work = crew.runner.delegate(parent.id, "engineer", "Improve the readme")
    await wait_until(crew.settled(work.id))
    if approved:
        await review_round(crew, parent, work, Verdict.PASS)
    if conflict:
        (repo / "README.md").write_text("user edit\n")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "user edit")
    head = git(repo, "rev-parse", "HEAD")
    store.set_auto_merge(enabled)
    commands = []
    original = worktree._git

    def recorded(cwd, *args, **kwargs):
        commands.append(args)
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", recorded)
    if not approved:
        with pytest.raises(NotAllowed, match="not approved"):
            crew.runner.propose_merge(parent.id, work.id, "Improve readme")
        assert store.get_task(work.id).merge_state is None
    elif conflict:
        with pytest.raises(MergeError, match="(?s)proposal is waiting for the user.*CONFLICT"):
            crew.runner.propose_merge(parent.id, work.id, "Improve readme")
        assert store.get_task(work.id).merge_state == "proposed"
        assert git(repo, "status", "--porcelain") == ""
        assert (repo / "README.md").read_text() == "user edit\n"
        assert git(repo, "rev-parse", "HEAD") == head
        assert not (repo / ".git" / "MERGE_HEAD").exists()
    else:
        result = crew.runner.propose_merge(parent.id, work.id, "Improve readme")
        assert result.merge_state == ("merged" if enabled else "proposed")
        assert (repo / "README.md").read_text() == ("bot edit\n" if enabled else "hello\n")
        if enabled:
            assert git(repo, "log", "-1", "--format=%s") == "Improve readme"
            assert git(repo, "log", "-1", "--format=%s", "HEAD^2") == "Improve readme"
            assert [e.data["automatic"] for e in store.events(work.id) if e.kind == "merged"] == [True]
            with pytest.raises(NotAllowed, match="already merged"):
                crew.runner.propose_merge(parent.id, work.id, "again")
        else:
            assert git(repo, "rev-parse", "HEAD") == head
    assert all("push" not in command for command in commands)


async def test_withdraw_proposal_saves_work_and_records_reason(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = crew_for(store, tmp_path)
    parent = store.create_task("chief", "lead", workspace=str(repo), status=Status.IDLE)
    work = crew.runner.delegate(parent.id, "engineer", "edit")
    await wait_until(crew.settled(work.id))
    await review_round(crew, parent, work, Verdict.PASS)
    crew.runner.propose_merge(parent.id, work.id, "Edit the readme")
    path = store.get_task(work.id).worktree_path
    Path(path, "new.txt").write_text("preserved")
    other = store.create_task("chief", "another coordinator", status=Status.IDLE)
    with pytest.raises(NotAllowed, match="not delegated by this task"):
        crew.runner.withdraw_merge(work.id, "Not mine", parent_id=other.id)
    assert store.get_task(work.id).merge_state == "proposed"
    with pytest.raises(NotAllowed, match="reason"):
        crew.runner.withdraw_merge(work.id, " ", parent_id=parent.id)
    task = crew.runner.withdraw_merge(work.id, "Superseded", parent_id=parent.id)
    assert task.merge_state is None and task.worktree_path == path
    assert not Path(path).exists()
    assert git(repo, "show", f"{task.branch}:new.txt") == "preserved"
    assert store.events(work.id)[-1].data == {"by": "chief", "reason": "Superseded"}
    with pytest.raises(NotAllowed, match="no merge proposal"):
        crew.runner.withdraw_merge(work.id, "again")
    crew.runner.propose_merge(parent.id, work.id, "Try again")
    assert crew.runner.withdraw_merge(work.id, "No longer needed").merge_state is None
    assert store.events(work.id)[-1].data["by"] == "user"


@pytest.mark.parametrize("action", ["withdraw", "merge"])
async def test_cleanup_failure_does_not_break_action(store, tmp_path, monkeypatch, caplog, action):
    repo = git_repo(tmp_path / "repo")
    crew = crew_for(store, tmp_path)
    parent = store.create_task("chief", "lead", workspace=str(repo), status=Status.IDLE)
    work = crew.runner.delegate(parent.id, "engineer", "edit")
    await wait_until(crew.settled(work.id))
    path = Path(store.get_task(work.id).worktree_path)
    (path / "new.txt").write_text("content")
    await review_round(crew, parent, work, Verdict.PASS)
    crew.runner.propose_merge(parent.id, work.id, "Edit files")
    original = worktree._git

    def fail_remove(cwd, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            raise worktree.GitError("simulated remove failure")
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", fail_remove)
    result = (crew.runner.merge(work.id) if action == "merge"
              else crew.runner.withdraw_merge(work.id, "withdrawn"))
    assert result.merge_state == ("merged" if action == "merge" else None)
    assert path.is_dir() and (path / "new.txt").read_text() == "content"
    assert "simulated remove failure" in caplog.text


async def test_merged_work_reopens_for_followup_review_and_changes_api(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = crew_for(store, tmp_path)
    parent = store.create_task("chief", "lead", workspace=str(repo), status=Status.IDLE)
    crew.script["engineer"] = lambda spec, me: (
        (spec.cwd / "new.txt").write_text("task work"), [Done(final_text="done")])[1]
    work = crew.runner.delegate(parent.id, "engineer", "edit")
    await wait_until(crew.settled(work.id))
    merged = crew.runner.merge(work.id, force=True)
    path = Path(merged.worktree_path)
    assert merged.merged_tip and not path.exists()

    app = create_app(crew.runner, token="admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765/v1") as client:
        response = await client.get(f"/tasks/{work.id}/changes", headers={"Authorization": "Bearer admin"})
    assert response.status_code == 200 and "+task work" in response.json()["diff"]

    store.update_task(work.id, merged_tip=None)  # older records recover the tip from the merge commit
    assert "+task work" in crew.runner.changes(work.id)

    crew.script["engineer"] = lambda spec, me: [Done(final_text=(spec.cwd / "new.txt").read_text())]
    crew.runner.send(work.id, "follow up")
    await wait_until(crew.settled(work.id))
    assert store.get_task(work.id).result == "task work"
    store.update_task(work.id, merged_tip=merged.merged_tip)
    assert crew.runner.drop_worktree(work.id)["status"] == "removed"
    assert not path.exists()

    crew.script["reviewer"] = lambda spec, me: [Done(final_text=(spec.cwd / "new.txt").read_text())]
    review = crew.runner.delegate(parent.id, "reviewer", "review", review_of=work.id)
    await wait_until(crew.settled(review.id))
    assert store.get_task(review.id).result == "task work"
    assert crew.runner.drop_worktree(work.id)["status"] == "removed"
    assert not path.exists()
    crew.runner.send(review.id, "review again")
    await wait_until(crew.settled(review.id))
    assert store.get_task(review.id).result == "task work"


def test_merge_subject_uses_first_paragraph_and_a_complete_sentence():
    from crewbots.runner.work import _merge_subject

    assert _merge_subject("A useful subject\n\nDetails", "title") == "A useful subject"
    assert _merge_subject("", "Fallback title") == "Fallback title"
    assert _merge_subject("e.g.", "Fallback title") == "Fallback title"
    assert _merge_subject("Fix the update dialog change list so it no longer cuts\noff mid-sentence", "title") == (
        "Fix the update dialog change list so it no longer cuts off mid-sentence")
    assert _merge_subject("Fix the update dialog change list so it no longer cuts\n"
                          "off mid-sentence when the subject exceeds the limit", "Fix the update dialog") == (
        "Fix the update dialog")
    assert _merge_subject("Fix the update dialog. " + "More detail " * 8, "title") == "Fix the update dialog."
    assert _merge_subject("e.g. " + "Details without a complete sentence " * 3, "title") == "title"
    assert _merge_subject("Fix examples such as e.g. the update dialog. " + "Details " * 8, "title") == (
        "Fix examples such as e.g. the update dialog.")
    assert _merge_subject("Fix the list, i.e. the update dialog. " + "Details " * 8, "title") == (
        "Fix the list, i.e. the update dialog.")
    assert _merge_subject("Fix updates, etc. " + "Details without a sentence " * 3, "title") == "title"


def test_merge_subject_fallback_is_nonempty_and_bounded():
    from crewbots.runner.work import _merge_subject

    long_summary = "A sentence that is much too long to fit into the permitted subject line " * 2
    assert _merge_subject(long_summary, "") == "Merge work"
    title = "A long task title with enough words that it cannot possibly fit in a single merge subject line"
    result = _merge_subject(long_summary, title)
    assert result == "A long task title with enough words that it cannot possibly fit in a…"
    assert len(result) <= 72
    assert _merge_subject(long_summary, "x" * 100) == "x" * 71 + "…"
    assert _merge_subject(long_summary, "x" * 70 + " next") == "x" * 70 + "…"


def engineer_wrote(crew, store, tmp_path, repo, content="new\n"):
    """Runs an engineer task to completion in its own worktree, writing README.md."""
    def engineer(spec, me):
        (spec.cwd / "README.md").write_text(content)
        return [Done(final_text="edited")]

    crew.script["engineer"] = engineer
    return crew.runner.submit("engineer", "edit the readme", workspace=str(repo))


async def test_unapproved_work_merges_only_when_forced(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = crew_for(store, tmp_path)
    work = engineer_wrote(crew, store, tmp_path, repo)
    await wait_until(crew.settled(work.id))

    with pytest.raises(MergeError, match="not approved"):
        crew.runner.merge(work.id)
    assert (repo / "README.md").read_text() == "hello\n"

    crew.runner.merge(work.id, force=True)
    assert (repo / "README.md").read_text() == "new\n"
    assert "Merged without approval" in git(repo, "log", "-1", "--format=%B")


async def test_a_dirty_checkout_is_never_merged_into(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = crew_for(store, tmp_path)
    work = engineer_wrote(crew, store, tmp_path, repo)
    await wait_until(crew.settled(work.id))
    (repo / "README.md").write_text("my own uncommitted edit\n")
    head = git(repo, "rev-parse", "HEAD")

    with pytest.raises(MergeError, match="uncommitted changes"):
        crew.runner.merge(work.id, force=True)

    assert (repo / "README.md").read_text() == "my own uncommitted edit\n"
    assert git(repo, "rev-parse", "HEAD") == head
    assert store.get_task(work.id).merge_state is None


async def test_a_conflict_is_aborted_and_changes_nothing(store, tmp_path):
    repo = git_repo(tmp_path / "repo")
    crew = crew_for(store, tmp_path)
    work = engineer_wrote(crew, store, tmp_path, repo, content="the bot's version\n")
    await wait_until(crew.settled(work.id))
    (repo / "README.md").write_text("the user's version\n")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "user edit")
    head = git(repo, "rev-parse", "HEAD")

    with pytest.raises(MergeError, match=r"(?s)nothing was changed.*CONFLICT.*README\.md"):
        crew.runner.merge(work.id, force=True)

    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == ""
    assert (repo / "README.md").read_text() == "the user's version\n"
    assert store.get_task(work.id).worktree_path  # still there to fix and retry
