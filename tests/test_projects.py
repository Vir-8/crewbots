"""The user's projects: folders the crew works in, one file each, that only the user adds or removes."""

import shutil

import pytest
from fakes import FakeBackend, bot, make_crew
from fastapi.testclient import TestClient

from crewbots import crew, discovery, paths, projects
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
    assert projects.listing(loaded) == (f"- web ({web}; each task in a worktree of its own): The company website: "
                                        "Next.js, deployed from main.")
    projects.delete(home, "web")
    assert projects.load(home) == ({}, {})


def test_bots_see_folders_under_home_with_a_tilde_and_others_in_full(tmp_path):
    crew, inside, beside = tmp_path / "crew", paths.home() / "code" / "web", paths.home().with_name("home-backup")
    for folder in (crew, inside, beside):
        folder.mkdir(parents=True)

    saved = {name: projects.save(crew, name, folder, "") for name, folder in (("web", inside), ("old", beside))}

    assert projects.listing(saved) == ("- web (~/code/web; each task in a worktree of its own)\n"
                                       f"- old ({beside}; each task in a worktree of its own)")
    assert "folder: ~/code/web" in (crew / "projects" / "web.md").read_text()


def test_a_projects_ci_list_is_read_and_kept_when_the_project_is_saved(tmp_path):
    folder = tmp_path / "web"
    folder.mkdir()
    (tmp_path / "crew" / "projects").mkdir(parents=True)
    path = tmp_path / "crew" / "projects" / "web.md"
    path.write_text(f"---\nfolder: {folder}\nci:\n  - make test\n---\nSite\n")
    assert projects.parse(path.read_text(), path).ci == ("make test",)
    assert projects.save(tmp_path / "crew", "web", folder, "Site, edited").ci == ("make test",)


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


def test_project_isolation_defaults_and_explicit_settings(tmp_path):
    home, folder, unity = tmp_path / "crew", tmp_path / "project", tmp_path / "unity"
    home.mkdir()
    folder.mkdir()
    (unity / "ProjectSettings").mkdir(parents=True)
    (unity / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.12f1\n")
    old = projects.parse(f"---\nfolder: {folder}\n---\nOld project", home / "old.md")
    assert old.isolation == "worktree"
    assert projects.save(home, "game", unity, "A game").isolation == "in_place"  # one Editor: the folder itself
    assert projects.save(home, "game", unity, "A game", isolation="worktree").isolation == "worktree"
    assert projects.save(home, "game", unity, "Updated").isolation == "worktree"
    assert projects.save(home, "project", folder, "A project", isolation="in_place").isolation == "in_place"
    assert projects.load(home)[0]["project"].isolation == "in_place"
    with pytest.raises(ProjectError, match="isolation must"):
        projects.parse(f"---\nfolder: {folder}\nisolation: invalid\n---\n", home / "bad.md")
    with pytest.raises(ProjectError, match="isolation must"):
        projects.save(home, "project", folder, "A project", isolation="branch")  # branch mode is gone
    assert projects.load(home)[0]["project"].isolation == "in_place"


def test_adding_a_unity_project_gives_the_crew_the_unity_verify_skill(tmp_path):
    home, web, game = crew.init(tmp_path / "crew"), tmp_path / "web", tmp_path / "game"
    web.mkdir()
    (game / "ProjectSettings").mkdir(parents=True)
    (game / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.3.12f1\n")

    projects.save(home, "web", web, "A website")
    assert "verify-unity" not in crew.load(home).skills
    projects.save(home, "web", game, "Now the game")  # an existing project moved to a Unity folder
    assert "verify-unity" in crew.load(home).skills
    projects.delete(home, "web")
    shutil.rmtree(home / "skills" / "verify-unity")
    projects.save(home, "game", game, "A game")
    assert "verify-unity" in crew.load(home).skills

    (home / "skills" / "verify-unity" / "SKILL.md").write_text("---\nname: verify-unity\ndescription: Mine\n---\n")
    projects.save(home, "game", game, "A game, edited")
    projects.save(home, "other-game", game, "The same game again")
    assert crew.load(home).skills["verify-unity"].description == "Mine"  # the crew's own copy is left alone


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


def test_project_isolation_api_validates_and_preserves_setting(client, tmp_path):
    folder = tmp_path / "game"
    (folder / "ProjectSettings").mkdir(parents=True)
    (folder / "ProjectSettings" / "ProjectVersion.txt").write_text("Unity")
    url = "/projects/game"
    created = client.put(url, json={"folder": str(folder)}, headers=AUTH)
    assert created.json()["isolation"] == "in_place"
    changed = client.put(url, json={"folder": str(folder), "isolation": "worktree"}, headers=AUTH)
    assert changed.json()["isolation"] == "worktree"
    assert client.put(url, json={"folder": str(folder), "about": "edited"}, headers=AUTH).json()["isolation"] == "worktree"
    assert client.put(url, json={"folder": str(folder), "isolation": "bad"}, headers=AUTH).status_code == 422
    assert client.get("/projects", headers=AUTH).json()["projects"][0]["isolation"] == "worktree"


def test_bringing_in_what_claude_code_and_codex_know(client, monkeypatch):
    lantern = paths.home() / "Games" / "Lantern"
    lantern.mkdir(parents=True)
    memory = paths.home() / ".claude" / "projects" / "lantern" / "memory" / "art.md"
    memory.parent.mkdir(parents=True)
    memory.write_text("Stylized art, never painted pavements.")
    found_memory = discovery.FoundMemory(memory, "claude", "Art", lantern)
    monkeypatch.setattr(discovery, "found", lambda: ([discovery.FoundFolder(lantern, {"claude"}, 3, 1.8e9, [found_memory],
                                                                             "lantern")], []))

    found = client.get("/projects/found", headers=AUTH).json()
    assert found["folders"][0]["name"] == "lantern" and found["folders"][0]["memories"][0]["title"] == "Art"
    brought = client.post("/projects/bring-in", headers=AUTH, json={
        "projects": [{"name": "lantern", "folder": str(lantern)}],
        "memories": [str(memory), "/etc/passwd"]}).json()  # only what was found comes in

    assert [p["name"] for p in brought["projects"]] == ["lantern"] and brought["memories"] == 1
    sent = [e.data for e in client.runner.store.events(brought["task_id"]) if e.kind == "message"]
    assert sent[0]["from"] == "crewbots" and "never painted pavements" in sent[0]["text"]
    assert "passwd" not in sent[0]["text"]
    assert client.runner.store.get_task(brought["task_id"]).bot == "chief"  # the coordinator learns from them

    # Looking again, what's in already says so, and so does a memory that's changed since.
    again = client.get("/projects/found", headers=AUTH).json()["folders"][0]
    assert again["project"] == "lantern"
    assert again["memories"][0]["brought_at"] and again["memories"][0]["changed"] is False
    memory.write_text("Stylized art, never painted pavements. Warm light.")
    assert client.get("/projects/found", headers=AUTH).json()["folders"][0]["memories"][0]["changed"] is True
