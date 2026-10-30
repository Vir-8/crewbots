"""Work done in a project's folder itself is committed there once approved: just the files it handed back, with their
Unity .meta files, on the folder's branch, leaving the rest of what's in progress there uncommitted."""

import dataclasses

import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until

from crewbots import worktree
from crewbots.backends import Access
from crewbots.crew_tools import merge_text
from crewbots.projects import Project
from crewbots.runner import MergeError, NotAllowed
from crewbots.store import Status, Store, Verdict


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def game(store, tmp_path):
    repo = git_repo(tmp_path / "game")
    (repo / "ProjectSettings").mkdir()
    (repo / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.12f1\n")
    worktree._git(repo, "add", "-A")
    worktree._git(repo, "commit", "-qm", "A Unity project")
    crew = ScriptedCrew(store, tmp_path / "crew", bot("chief", can_delegate=True),
                        bot("engineer", access=Access.WORKSPACE_WRITE), bot("reviewer"))
    project = Project("game", repo, "A game", tmp_path / "game.md")  # a Unity project: worked in its folder itself
    crew.runner.reload(dataclasses.replace(crew.runner.crew, projects={"game": project}))
    lead = store.create_task("chief", "lead", status=Status.IDLE)
    work = store.create_task("engineer", "add the player and a bat", parent_id=lead.id, created_by="chief",
                             workspace=str(repo), status=Status.IDLE)
    store.update_task(work.id, turns=1)
    write = lambda path, text="x": ((repo / path).parent.mkdir(parents=True, exist_ok=True),  # noqa: E731
                                    (repo / path).write_text(text))
    for path in ("Assets/Player.cs", "Assets/Player.cs.meta", "Assets/Enemies/Bat.cs", "Assets/Enemies/Bat.cs.meta",
                 "Assets/Enemies.meta", "Assets/Other.cs"):  # Other.cs: another engineer's, in progress
        write(path)
    write("README.md", "the user's own edit\n")
    yield crew, store, repo, lead, store.get_task(work.id)


async def test_approved_work_is_committed_with_just_its_files(game):
    crew, store, repo, lead, work = game
    with pytest.raises(NotAllowed, match="evidence"):
        crew.runner.handoff(work.id, "Added the player and a bat", ["Assets/Player.cs", "Assets/Enemies/"], "", "")
    crew.runner.handoff(work.id, "Added the player and a bat", ["Assets/Player.cs", "Assets/Enemies/"], "", "",
                        ["Played SampleScene: the bat follows the player"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))
    [spec] = crew.specs_for("reviewer")
    assert "only the files it handed back are its own: Assets/Enemies.meta, Assets/Enemies/Bat.cs" in spec.system_prompt
    crew.runner.review(review.id, Verdict.PASS, "fine", [])
    store.set_auto_merge(True)

    merged = await crew.runner.propose_merge_off_loop(lead.id, work.id, "Add the player and a bat that follows it")

    assert merged.merge_state == "merged" and merged.merged_commit == worktree._git(repo, "rev-parse", "HEAD")
    committed = worktree._git(repo, "show", "--name-only", "--format=%s", "HEAD").splitlines()
    assert committed == ["Add the player and a bat that follows it", "", "Assets/Enemies.meta", "Assets/Enemies/Bat.cs",
                         "Assets/Enemies/Bat.cs.meta", "Assets/Player.cs", "Assets/Player.cs.meta"]
    left = [line.strip() for line in worktree._git(repo, "status", "--porcelain", "-uall").splitlines()]
    assert left == ["M README.md", "?? Assets/Other.cs"]  # the user's edit and the other engineer's, still uncommitted
    assert "+++ b/Assets/Player.cs" in crew.runner.changes(work.id)  # what the app shows of it
    [item] = crew.runner.landed()["items"]
    assert item["commit"] == merged.merged_commit and item["evidence"] == ["Played SampleScene: the bat follows the player"]
    await crew.runner.shutdown()


async def test_a_summary_whose_first_line_is_too_long_is_refused_before_anything_is_committed(game):
    crew, store, repo, lead, work = game
    crew.runner.handoff(work.id, "Added the player", ["Assets/Player.cs"], "", "", ["Played it"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))
    crew.runner.review(review.id, Verdict.PASS, "fine", [])
    with pytest.raises(NotAllowed, match="it is 73 characters, over the limit of 72"):
        crew.runner.propose_merge(lead.id, work.id, "x" * 73 + "\n\nDetails")
    for blank_first_line in ("\nFix the player", "  \nFix the player"):
        with pytest.raises(NotAllowed, match="is empty"):
            crew.runner.propose_merge(lead.id, work.id, blank_first_line)
    assert store.get_task(work.id).merge_state is None
    assert worktree._git(repo, "rev-list", "--count", "HEAD") == "2"
    await crew.runner.shutdown()


async def test_work_that_names_none_of_its_files_isnt_committed(game):
    crew, store, repo, lead, work = game
    crew.runner.handoff(work.id, "Added the player", ["the player script"], "", "")
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))
    crew.runner.review(review.id, Verdict.PASS, "fine", [])
    with pytest.raises(NotAllowed, match="handed back none of the files it changed"):
        crew.runner.propose_merge(lead.id, work.id, "Add the player")
    assert worktree._git(repo, "rev-list", "--count", "HEAD") == "2"  # nothing committed
    await crew.runner.shutdown()


async def test_output_git_ignores_is_named_rather_than_left_out_quietly(game):
    crew, store, repo, lead, work = game
    (repo / ".gitignore").write_text("*.md\n**/.*/\n")
    worktree._git(repo, "add", ".gitignore")
    worktree._git(repo, "commit", "-qm", "Ignore notes")
    (repo / ".agents" / "skills" / "verify").mkdir(parents=True)
    (repo / ".agents" / "skills" / "verify" / "SKILL.md").write_text("# Verify\n")
    skill = ".agents/skills/verify/SKILL.md"
    crew.runner.handoff(work.id, "Wrote the verify skill", [skill], "", "", ["followed it once"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))
    crew.runner.review(review.id, Verdict.PASS, "fine", [])
    with pytest.raises(NotAllowed, match=f"git ignores what {work.id} handed back .*\\({skill}\\).*drop_task"):
        crew.runner.propose_merge(lead.id, work.id, "Add a verify skill")

    store.update_task(work.id, turns=2)
    crew.runner.handoff(work.id, "Wrote the verify skill and the player", [skill, "Assets/Player.cs"], "", "", ["ran it"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it again", review_of=work.id)
    await wait_until(crew.settled(review.id))
    crew.runner.review(review.id, Verdict.PASS, "fine", [])
    store.set_auto_merge(True)
    merged = await crew.runner.propose_merge_off_loop(lead.id, work.id, "Add the player")
    committed = worktree._git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["Assets/Player.cs", "Assets/Player.cs.meta"]
    event = store.last_event(work.id, ("merged",))
    assert event.data["ignored"] == [skill]
    assert f"Left out, as git ignores them in this project: {skill}." in merge_text(
        work.id, merged.merge_state, merged.merged_commit, event.data)
    await crew.runner.shutdown()


async def test_work_isnt_committed_onto_a_branch_the_folder_was_switched_to(game):
    crew, store, repo, lead, work = game
    crew.runner._cwd(work, crew.runner.crew.bots["engineer"])  # its first turn notes the folder's branch
    crew.runner.handoff(work.id, "Added the player", ["Assets/Player.cs"], "", "", ["ran it"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))
    crew.runner.review(review.id, Verdict.PASS, "fine", [])
    worktree._git(repo, "switch", "-q", "-c", "engineer-1/player")  # what an engineer shouldn't do
    store.set_auto_merge(True)
    with pytest.raises(MergeError, match="is on engineer-1/player now, not main"):
        await crew.runner.propose_merge_off_loop(lead.id, work.id, "Add the player")
    assert worktree._git(repo, "rev-list", "--count", "HEAD") == "2"
    await crew.runner.shutdown()


async def _staged_move(game, output, *, rename=True):
    crew, store, repo, lead, work = game
    (repo / "Assets" / "Old.cs").write_text("class Old {}\n")
    worktree._git(repo, "add", "Assets/Old.cs")
    worktree._git(repo, "commit", "-qm", "Add Old")
    if rename:
        worktree._git(repo, "mv", "Assets/Old.cs", "Assets/Moved.cs")
    else:
        worktree._git(repo, "rm", "-q", "Assets/Old.cs")
        (repo / "Assets" / "Moved.cs").write_text("class Old {}\n")
        worktree._git(repo, "add", "Assets/Moved.cs")
    worktree._git(repo, "add", "Assets/Other.cs")
    crew.runner.handoff(work.id, "Moved Old", output, "", "", ["ran it"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))
    crew.runner.review(review.id, Verdict.PASS, "fine", [])
    store.set_auto_merge(True)
    return crew, repo, lead, work


def _left(repo):
    return [line.strip() for line in worktree._git(repo, "status", "--porcelain", "-uall").splitlines()]


async def test_a_staged_rename_with_both_paths_is_committed_as_one_beside_someone_elses_staged_change(game):
    crew, repo, lead, work = await _staged_move(game, ["Assets/Old.cs", "Assets/Moved.cs"])

    await crew.runner.propose_merge_off_loop(lead.id, work.id, "Move Old")

    renamed = worktree._git(repo, "show", "--name-status", "--format=", "HEAD").split()
    assert renamed == ["R100", "Assets/Old.cs", "Assets/Moved.cs"]
    assert "A  Assets/Other.cs" in _left(repo) and not [line for line in _left(repo) if "Old.cs" in line or "Moved.cs" in line]
    await crew.runner.shutdown()


@pytest.mark.parametrize("output, missing", [(["Assets/Moved.cs"], "Assets/Old.cs"), (["Assets/Old.cs"], "Assets/Moved.cs")])
async def test_a_staged_rename_with_one_path_handed_back_is_refused_naming_the_other(game, output, missing):
    crew, repo, lead, work = await _staged_move(game, output)
    head = worktree._git(repo, "rev-parse", "HEAD")

    with pytest.raises(MergeError, match=f"{missing} is the other side.*list both"):
        await crew.runner.propose_merge_off_loop(lead.id, work.id, "Move Old")

    assert worktree._git(repo, "rev-parse", "HEAD") == head
    assert "A  Assets/Other.cs" in _left(repo)
    await crew.runner.shutdown()


async def test_someone_elses_staged_deletion_isnt_committed_with_an_identical_new_file(game):
    crew, repo, lead, work = await _staged_move(game, ["Assets/Moved.cs"], rename=False)
    head = worktree._git(repo, "rev-parse", "HEAD")

    with pytest.raises(MergeError, match="Assets/Old.cs is the other side"):
        await crew.runner.propose_merge_off_loop(lead.id, work.id, "Add Moved")

    assert worktree._git(repo, "rev-parse", "HEAD") == head
    assert "R  Assets/Old.cs -> Assets/Moved.cs" in _left(repo)
    await crew.runner.shutdown()
