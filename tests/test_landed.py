"""What landed: merged work listed with its evidence and verdicts, what's new since the user looked, and reverting."""

import httpx
import pytest
from fakes import ScriptedCrew, bot, git_repo, wait_until

from crewbots import worktree
from crewbots.api import create_app
from crewbots.backends import Access
from crewbots.events import Done
from crewbots.runner.common import NotAllowed
from crewbots.store import Status, Store, Verdict


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


async def merged_work(store, tmp_path, crew, text="hello"):
    repo = tmp_path / "repo" if (tmp_path / "repo").exists() else git_repo(tmp_path / "repo")
    parent = store.create_task("chief", "lead", workspace=str(repo), status=Status.IDLE)
    crew.script["engineer"] = lambda spec, me: (
        (spec.cwd / f"{text}.txt").write_text(text),
        crew.runner.handoff(me.id, f"Added {text}.txt", [], "", "", [f"cat {text}.txt → {text}"]),
        [Done(final_text="done")])[2]
    work = crew.runner.delegate(parent.id, "engineer", f"add {text}")
    await wait_until(crew.settled(work.id))
    review = crew.runner.delegate(parent.id, "reviewer", "review", review_of=work.id)
    await wait_until(crew.settled(review.id))
    crew.runner.review(review.id, Verdict.PASS, "Reran it: fine.", [])
    crew.runner.merge(work.id)
    return repo, store.get_task(work.id)


async def test_what_landed_shows_its_evidence_and_verdicts_and_what_is_new(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True),
                        bot("engineer", access=Access.WORKSPACE_WRITE), bot("reviewer"))
    _, first = await merged_work(store, tmp_path, crew, "one")
    assert crew.runner.state()["landed"] == 1
    crew.runner.mark_landed_seen()
    assert crew.runner.state()["landed"] == 0
    _, second = await merged_work(store, tmp_path, crew, "two")
    landed = crew.runner.landed()
    assert [i["task"]["id"] for i in landed["items"]] == [second.id, first.id]  # the latest first
    item = landed["items"][0]
    assert item["evidence"] == ["cat two.txt → two"] and item["commit"] == second.merged_commit
    assert item["verdicts"] == [{"bot": "reviewer", "verdict": "pass", "summary": "Reran it: fine."}]
    assert crew.runner.state()["landed"] == 1 and landed["seen_at"]
    await crew.runner.shutdown()


async def test_reverting_landed_work_is_a_commit_of_its_own(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief", can_delegate=True),
                        bot("engineer", access=Access.WORKSPACE_WRITE), bot("reviewer"))
    repo, work = await merged_work(store, tmp_path, crew)
    assert (repo / "hello.txt").exists()
    (repo / "README.md").write_text("an edit of the user's")
    with pytest.raises(NotAllowed, match="uncommitted changes"):
        crew.runner.revert(work.id)
    worktree._git(repo, "checkout", "README.md")

    app = create_app(crew.runner, token="admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8765/v1") as client:
        response = await client.post(f"/tasks/{work.id}/revert", headers={"Authorization": "Bearer admin"})
        assert response.status_code == 200
        again = await client.post(f"/tasks/{work.id}/revert", headers={"Authorization": "Bearer admin"})
        assert again.status_code == 403 and "already" in again.text
        landed = (await client.get("/landed", headers={"Authorization": "Bearer admin"})).json()
    assert not (repo / "hello.txt").exists()
    assert worktree._git(repo, "log", "-1", "--format=%s").startswith("Revert ")
    [item] = landed["items"]
    assert item["reverted"]["commit"] == work.merged_commit and item["reverted"]["by"] == "user"
    await crew.runner.shutdown()
