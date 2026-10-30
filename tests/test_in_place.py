"""Work done in a project's folder itself is committed there once approved: just the files it handed back, with their
Unity .meta files, on the folder's branch, leaving the rest of what's in progress there uncommitted."""

import dataclasses
import subprocess

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


async def _staged_move(game, output):
    crew, store, repo, lead, work = game
    (repo / "Assets" / "Old.cs").write_text("class Old {}\n")
    worktree._git(repo, "add", "Assets/Old.cs")
    worktree._git(repo, "commit", "-qm", "Add Old")
    worktree._git(repo, "mv", "Assets/Old.cs", "Assets/Moved.cs")
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


async def _handed_back(game, output):
    crew, store, repo, lead, work = game
    crew.runner.handoff(work.id, "Ignored Notes", output, "", "", ["ran it"])
    review = crew.runner.delegate(lead.id, "reviewer", "review it", review_of=work.id)
    await wait_until(crew.settled(review.id))
    crew.runner.review(review.id, Verdict.PASS, "fine", [])
    store.set_auto_merge(True)
    return crew, repo, lead, work


async def test_ignoring_staged_deletions_commits_them_and_keeps_the_files(game):
    crew, store, repo, lead, work = game
    for name in ("A.txt", "B.txt"):
        (repo / name).write_text(f"{name}\n")
    worktree._git(repo, "add", "A.txt", "B.txt")
    worktree._git(repo, "commit", "-qm", "Add notes")
    worktree._git(repo, "rm", "-q", "--cached", "A.txt", "B.txt")
    (repo / ".gitignore").write_text("A.txt\nB.txt\n")
    crew, repo, lead, work = await _handed_back(game, [".gitignore", "A.txt", "B.txt"])

    await crew.runner.propose_merge_off_loop(lead.id, work.id, "Ignore notes")

    assert worktree._git(repo, "show", "--no-renames", "--name-status", "--format=", "HEAD").split() == [
        "A", ".gitignore", "D", "A.txt", "D", "B.txt"]
    assert [(repo / n).read_text() for n in ("A.txt", "B.txt")] == ["A.txt\n", "B.txt\n"]
    assert not [line for line in _left(repo) if "A.txt" in line or "B.txt" in line or ".gitignore" in line]
    assert not list((repo / ".git").glob("crewbots-index-*"))
    await crew.runner.shutdown()


async def test_a_held_index_lock_refuses_the_merge_before_anything_is_committed(game):
    crew, store, repo, lead, work = game
    crew, repo, lead, work = await _handed_back(game, ["Assets/Player.cs", "Assets/Player.cs.meta"])
    head = worktree._git(repo, "rev-parse", "HEAD")
    (repo / ".git" / "index.lock").write_text("")

    with pytest.raises(MergeError, match="index.lock"):
        await crew.runner.propose_merge_off_loop(lead.id, work.id, "Add the player")

    assert worktree._git(repo, "rev-parse", "HEAD") == head
    await crew.runner.shutdown()


async def test_a_lock_taken_after_the_commit_still_records_the_merge_and_says_how_to_sync(game, monkeypatch):
    crew, store, repo, lead, work = game
    (game[2] / "Assets/space name.cs").write_text("x")
    crew, repo, lead, work = await _handed_back(game, ["Assets/Player.cs", "Assets/space name.cs"])
    real = worktree._git

    def locking(cwd, *args, **kwargs):
        if args[:1] == ("reset",):
            (repo / ".git" / "index.lock").write_text("")
        return real(cwd, *args, **kwargs)

    monkeypatch.setattr(worktree, "_git", locking)
    await crew.runner.propose_merge_off_loop(lead.id, work.id, "Add the player")
    monkeypatch.setattr(worktree, "_git", real)

    merged = store.last_event(work.id, ("merged",))
    assert store.get_task(work.id).merged_commit == real(repo, "rev-parse", "HEAD")
    command = merged.data["index"].split("run ")[1].removesuffix(" once the lock clears")
    assert "'Assets/space name.cs'" in command
    (repo / ".git" / "index.lock").unlink()
    subprocess.run(command, shell=True, cwd=repo, check=True)
    assert "space name" not in real(repo, "status", "--porcelain", "Assets/Player.cs", "Assets/space name.cs")
    await crew.runner.shutdown()
