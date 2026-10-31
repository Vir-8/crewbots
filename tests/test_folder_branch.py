"""A project can cap its task worktrees, and lend its folder to one task at a time on a branch of its own, which takes
no worktree: the folder switches to the task's branch, and back once its work is merged or dropped."""

import dataclasses

import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until

from crewbots import projects, worktree
from crewbots.backends import Access
from crewbots.events import Done
from crewbots.projects import Project, ProjectError
from crewbots.runner import NotAllowed
from crewbots.store import Status, Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def crew_with(store, tmp_path, **settings):
    """A lead and two engineers, and the project `game` (a repository) with `settings`."""
    repo = git_repo(tmp_path / "game")
    crew = ScriptedCrew(store, tmp_path / "crew", bot("lead", can_delegate=True, access=Access.WORKSPACE_WRITE),
                        *(bot(f"engineer-{n}", access=Access.WORKSPACE_WRITE) for n in (1, 2, 3)))
    project = Project("game", repo, "A game", tmp_path / "game.md", "worktree", **settings)
    crew.runner.reload(dataclasses.replace(crew.runner.crew, projects={"game": project}))
    return crew, repo, store.create_task("lead", "lead", status=Status.IDLE)


def head(repo):
    return worktree._git(repo, "symbolic-ref", "--short", "HEAD")


def test_the_settings_are_kept_in_the_project_file(tmp_path):
    folder, home = tmp_path / "game", tmp_path / "crew"
    folder.mkdir()
    saved = projects.save(home, "game", folder, "A game", max_worktrees=2, folder_branch=True)
    assert (saved.max_worktrees, saved.folder_branch) == (2, True)
    assert "max_worktrees: 2" in saved.path.read_text()
    kept = projects.save(home, "game", folder, "Still a game")  # left out: kept
    assert (kept.max_worktrees, kept.folder_branch) == (2, True)
    off = projects.save(home, "game", folder, "A game", max_worktrees=0, folder_branch=False)
    assert (off.max_worktrees, off.folder_branch) == (None, False)
    assert "max_worktrees" not in off.path.read_text()
    assert "at most 2 at once" in projects.listing({"game": kept}) and "in_folder" in projects.listing({"game": kept})
    for bad in ("max_worktrees: 0", "max_worktrees: two", "folder_branch: maybe"):
        with pytest.raises(ProjectError):
            projects.parse(f"---\nfolder: {folder}\n{bad}\n---\n", tmp_path / "game.md")


async def test_work_past_the_cap_is_refused_until_a_worktree_goes(store, tmp_path):
    crew, repo, lead = crew_with(store, tmp_path, max_worktrees=2)
    first = crew.runner.delegate(lead.id, "engineer-1", "fix the camera", project="game")
    crew.runner.delegate(lead.id, "engineer-2", "trace the aim", project="game")  # waiting to start counts too
    with pytest.raises(NotAllowed, match=rf"game allows at most 2 task worktrees at once.*{first.id} \(engineer-1"):
        crew.runner.delegate(lead.id, "engineer-3", "and a third", project="game")
    await wait_until(lambda: not crew.runner.running())
    assert all(store.get_task(t.id).worktree_path for t in store.children(lead.id))

    with pytest.raises(NotAllowed, match="at most 2"):
        crew.runner.start_thread(lead.id, "Mine", "my own", project="game")
    await crew.runner.drop(first.id, "not needed", by_task=lead.id)
    third = crew.runner.delegate(lead.id, "engineer-3", "and a third", project="game")
    await wait_until(crew.settled(third.id))
    assert store.get_task(third.id).worktree_path
    await crew.runner.shutdown()


async def test_the_cap_holds_when_a_task_makes_its_worktree(store, tmp_path):
    crew, repo, lead = crew_with(store, tmp_path)
    one = crew.runner.delegate(lead.id, "engineer-1", "one", project="game")
    await wait_until(crew.settled(one.id))
    crew.runner.reload(dataclasses.replace(crew.runner.crew, projects={
        "game": dataclasses.replace(crew.runner.crew.projects["game"], max_worktrees=1)}))
    two = store.create_task("engineer-2", "two", parent_id=lead.id, created_by="lead", workspace=str(repo))
    crew.runner._start(two.id, "two")  # as a user's thread would, past the check at delegation
    await wait_until(crew.settled(two.id))
    assert store.get_task(two.id).status is Status.FAILED and "at most 1" in store.get_task(two.id).error
    assert not store.get_task(two.id).worktree_path
    await crew.runner.shutdown()


async def test_a_task_works_in_the_folder_on_its_own_branch_and_merges_back(store, tmp_path):
    crew, repo, lead = crew_with(store, tmp_path, max_worktrees=1, folder_branch=True)
    crew.script["engineer-1"] = lambda spec, me: [(spec.cwd / "camera.cs").write_text("smooth") and Done(final_text="done")]
    work = crew.runner.delegate(lead.id, "engineer-1", "fix the camera", project="game", in_folder=True)
    with pytest.raises(NotAllowed, match=f"{work.id} \\(engineer-1\\) has game's folder"):
        crew.runner.delegate(lead.id, "engineer-2", "me too", project="game", in_folder=True)
    other = crew.runner.delegate(lead.id, "engineer-2", "in a worktree", project="game")  # the folder takes no worktree
    await wait_until(lambda: not crew.runner.running())

    work = store.get_task(work.id)
    assert [s.cwd for s in crew.specs_for("engineer-1")] == [repo]
    assert work.worktree_path == work.repo == str(repo) and head(repo) == f"crewbots/{work.id}"
    assert store.get_task(other.id).worktree_path != str(repo)
    assert not crew.runner.already_on(work)

    merged = crew.runner.merge(work.id, force=True)
    assert merged.merge_state == "merged"
    assert head(repo) == "main" and (repo / "camera.cs").read_text() == "smooth"
    assert worktree._git(repo, "status", "--porcelain") == ""
    assert f"crewbots/{work.id}" not in worktree._git(repo, "branch")
    again = crew.runner.delegate(lead.id, "engineer-3", "next", project="game", in_folder=True)  # free again
    await wait_until(crew.settled(again.id))
    assert head(repo) == f"crewbots/{again.id}"
    await crew.runner.shutdown()


async def test_dropped_folder_work_is_kept_on_its_branch_and_the_folder_goes_back(store, tmp_path):
    crew, repo, lead = crew_with(store, tmp_path, folder_branch=True)
    seen = []

    def engineer(spec, me):
        seen.append((head(spec.cwd), (spec.cwd / "draft.cs").exists()))
        (spec.cwd / "draft.cs").write_text("wip")
        return [Done(final_text="done")]
    crew.script["engineer-1"] = engineer
    work = crew.runner.delegate(lead.id, "engineer-1", "try it", project="game", in_folder=True)
    await wait_until(crew.settled(work.id))

    await crew.runner.drop(work.id, "a better idea came", by_task=lead.id)
    assert head(repo) == "main" and not (repo / "draft.cs").exists()
    assert worktree._git(repo, "show", f"crewbots/{work.id}:draft.cs") == "wip"
    crew.runner.send(work.id, "pick it up again", sender="lead")  # its next turn takes the folder back
    await wait_until(crew.settled(work.id))
    assert seen == [(f"crewbots/{work.id}", False), (f"crewbots/{work.id}", True)]
    assert head(repo) == "main"  # and, still dropped, gives it back after
    await crew.runner.shutdown()


async def test_the_folder_is_lent_only_when_turned_on_and_clean(store, tmp_path):
    crew, repo, lead = crew_with(store, tmp_path)
    with pytest.raises(NotAllowed, match="doesn't lend its folder"):
        crew.runner.delegate(lead.id, "engineer-1", "here", project="game", in_folder=True)
    crew, repo, lead = crew_with(store, tmp_path / "again", folder_branch=True)
    (repo / "README.md").write_text("the user's edit")
    with pytest.raises(NotAllowed, match="1 uncommitted change"):
        crew.runner.delegate(lead.id, "engineer-1", "here", project="game", in_folder=True)
    worktree._git(repo, "checkout", "README.md")
    worktree._git(repo, "switch", "-qc", "crewbots/t_old")
    with pytest.raises(NotAllowed, match="a task's branch"):
        crew.runner.delegate(lead.id, "engineer-1", "here", project="game", in_folder=True)
    with pytest.raises(NotAllowed, match="name the project"):
        crew.runner.delegate(lead.id, "engineer-1", "here", in_folder=True)
    await crew.runner.shutdown()
