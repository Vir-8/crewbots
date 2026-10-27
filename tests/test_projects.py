"""The user's projects: folders the crew works in, one file each, that only the user adds or removes."""

import pytest
from fakes import FakeBackend, bot, make_crew
from fastapi.testclient import TestClient

from crewbots import discovery, paths, projects
from crewbots.api import create_app
from crewbots.projects import ProjectError
from crewbots.runner import Runner
from crewbots.store import Store

TOKEN, PORT = "t" * 32, 8765
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_a_project_is_a_folder_and_what_it_is(tmp_path):
    home, web = tmp_path / "crew", tmp_path / "code" / "web"
    web.mkdir(parents=True)
    home.mkdir()

    saved = projects.save(home, "web", web, "The company website: Next.js, deployed from main.\n\nMore detail.")

    assert saved.folder == web and saved.gist == "The company website: Next.js, deployed from main."
    loaded, errors = projects.load(home)
    assert loaded == {"web": saved} and errors == {}
    assert projects.listing(loaded) == f"- web ({web}): The company website: Next.js, deployed from main."
    projects.delete(home, "web")
    assert projects.load(home) == ({}, {})


def test_only_real_folders_outside_the_crew_and_names_that_make_good_handles(tmp_path):
    home = tmp_path / "crew"
    home.mkdir()
    with pytest.raises(ProjectError, match="isn't a folder"):
        projects.save(home, "gone", tmp_path / "nowhere", "")
    with pytest.raises(ProjectError, match="crew's own settings"):
        projects.save(home, "crew", home, "")
    with pytest.raises(ProjectError, match="crew's own settings"):
        projects.save(home, "above", tmp_path, "")  # a folder holding the crew
    with pytest.raises(ProjectError, match="isn't a project name"):
        projects.save(home, "My Game", tmp_path, "")
    assert projects.name_for(tmp_path / "My Game (2026)") == "my-game-2026"


def test_a_file_that_cant_be_read_doesnt_lose_the_rest(tmp_path):
    folder = tmp_path / "projects"
    folder.mkdir()
    (folder / "game.md").write_text(f"---\nfolder: {tmp_path}\n---\nA game.\n")
    (folder / "broken.md").write_text("no frontmatter")

    loaded, errors = projects.load(tmp_path)

    assert list(loaded) == ["game"] and "frontmatter" in errors["broken.md"]


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path / "crew", bot("chief", can_delegate=True), bot("engineer")),
                    {"fake": FakeBackend()})
    with TestClient(create_app(runner, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}/v1") as c:
        c.runner = runner
        yield c
    store.close()


def test_the_user_adds_and_removes_projects_and_the_crew_sees_them_at_once(client, tmp_path):
    game = tmp_path / "game"
    game.mkdir()

    added = client.put("/projects/game", json={"folder": str(game), "about": "A cozy game."}, headers=AUTH)
    assert added.status_code == 200 and added.json()["missing"] is False
    assert client.get("/projects", headers=AUTH).json()["projects"][0]["name"] == "game"
    assert "game" in client.runner.crew.projects
    assert client.put("/projects/home", json={"folder": str(tmp_path / "crew")}, headers=AUTH).status_code == 422
    assert client.delete("/projects/game", headers=AUTH).status_code == 204
    assert client.runner.crew.projects == {}


def test_bringing_in_what_claude_code_and_codex_know(client, monkeypatch):
    sky = paths.home() / "Games" / "Sky Islands"
    sky.mkdir(parents=True)
    memory = paths.home() / ".claude" / "projects" / "sky" / "memory" / "art.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Stylized art, never painted pavements.")
    found_memory = discovery.FoundMemory(memory, "claude", "Art", sky)
    monkeypatch.setattr(discovery, "found", lambda: ([discovery.FoundFolder(sky, {"claude"}, 3, 1.8e9, [found_memory],
                                                                             "sky-islands")], []))

    found = client.get("/projects/found", headers=AUTH).json()
    assert found["folders"][0]["name"] == "sky-islands" and found["folders"][0]["memories"][0]["title"] == "Art"
    brought = client.post("/projects/bring-in", headers=AUTH, json={
        "projects": [{"name": "sky-islands", "folder": str(sky)}],
        "memories": [str(memory), "/etc/passwd"]}).json()  # only what was found comes in

    assert [p["name"] for p in brought["projects"]] == ["sky-islands"] and brought["memories"] == 1
    sent = [e.data for e in client.runner.store.events(brought["task_id"]) if e.kind == "message"]
    assert sent[0]["from"] == "crewbots" and "never painted pavements" in sent[0]["text"]
    assert "passwd" not in sent[0]["text"]
    assert client.runner.store.get_task(brought["task_id"]).bot == "chief"  # the coordinator learns from them

    # Looking again, what's in already says so, and so does a memory that's changed since.
    again = client.get("/projects/found", headers=AUTH).json()["folders"][0]
    assert again["project"] == "sky-islands"
    assert again["memories"][0]["brought_at"] and again["memories"][0]["changed"] is False
    memory.write_text("Stylized art, never painted pavements. Warm light.")
    assert client.get("/projects/found", headers=AUTH).json()["folders"][0]["memories"][0]["changed"] is True
