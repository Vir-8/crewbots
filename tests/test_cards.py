"""Editing role cards and routines from the app: every change is checked before the file is replaced."""

import pytest
from fakes import FakeBackend
from fastapi.testclient import TestClient

from crewbots import cards, crew
from crewbots.api import create_app
from crewbots.crew import CrewError
from crewbots.routines import RoutineError
from crewbots.runner import Runner
from crewbots.store import Store


@pytest.fixture
def home(tmp_path):
    return crew.init(tmp_path / "crew")


def test_editing_a_card_keeps_what_the_app_doesnt_edit(home):
    path = home / "bots" / "engineer-1.md"
    path.write_text(path.read_text().replace("access: workspace_write\n", "access: workspace_write\ntimeout: 900\n"))

    cards.save_card(home, "engineer-1", {"display_name": "Ada", "avatar": {"color": "orange", "shape": "hex"},
                                       "network": True}, "## Mission\n\nShip it.")

    bot = crew.load(home).bots["engineer-1"]
    assert bot.label == "Ada" and bot.network is True and bot.timeout_s == 900
    assert bot.instructions.startswith("## Mission\n\nShip it.")
    cards.save_card(home, "engineer-1", {"network": False}, None)
    assert crew.load(home).bots["engineer-1"].network is False and "network" not in path.read_text()


def test_a_bad_edit_is_refused_and_the_card_is_unchanged(home):
    path = home / "bots" / "critic.md"
    before = path.read_text()

    with pytest.raises(CrewError, match="list of hosts"):  # the critic is a Codex bot; it can't keep to hosts
        cards.save_card(home, "critic", {"access": "workspace_write", "network": ["pypi.org"]}, None)
    assert path.read_text() == before


def test_a_new_bot_needs_a_handle_of_its_own(home):
    cards.save_card(home, "designer", {"title": "Designer", "description": "Draws the UI.", "backend": "claude"},
                    "## Mission\n\nDesign.", create=True)

    assert crew.load(home).bots["designer"].title == "Designer"
    with pytest.raises(CrewError, match="already a bot"):
        cards.save_card(home, "designer", {"description": "x"}, "", create=True)
    with pytest.raises(CrewError, match="handle"):
        cards.save_card(home, "Big Bot", {"description": "x"}, "", create=True)


def test_routines_are_edited_created_and_deleted(home):
    cards.save_routine(home, "standup", {"enabled": False, "schedule": "30 8 * * 1-5"}, None)
    assert cards.routine(home, "standup") | {"goal": ""} == {
        "name": "standup", "description": cards.routine(home, "standup")["description"], "bot": "chief",
        "schedule": "30 8 * * 1-5", "on": None, "repo": None, "workspace": None, "budget": None, "enabled": False,
        "proposed_by": None, "goal": ""}

    cards.save_routine(home, "ci", {"bot": "chief", "on": "github.ci_failed", "repo": "vir/app"}, "Fix {{pr.url}}",
                       create=True)
    assert cards.routine(home, "ci")["on"] == "github.ci_failed"
    with pytest.raises(RoutineError, match="schedule or an on trigger"):
        cards.save_routine(home, "broken", {"bot": "chief"}, "x", create=True)
    cards.delete_routine(home, "ci")
    with pytest.raises(KeyError):
        cards.routine(home, "ci")


def test_editing_over_the_api_takes_effect_at_once(home, tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, crew.load(home), {"claude": FakeBackend(), "codex": FakeBackend()})
    auth = {"Authorization": "Bearer t"}
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        assert client.get("/bots/chief/card", headers=auth).json()["title"] == "Chief of Staff"
        saved = client.put("/bots/chief/card", json={"display_name": "Roger"}, headers=auth).json()
        assert saved["display_name"] == "Roger" and runner.crew.bots["chief"].label == "Roger"
        assert client.put("/bots/chief/card", json={"access": "full", "network": ["x.org"]},
                          headers=auth).status_code == 200  # a Claude bot can keep to hosts
        assert client.put("/bots/nobody/card", json={}, headers=auth).status_code == 404
        made = client.post("/bots", json={"name": "designer", "description": "Draws.", "instructions": "Draw."},
                           headers=auth)
        assert made.status_code == 201 and "designer" in runner.crew.bots
        assert client.put("/routines/standup/file", json={"enabled": False}, headers=auth).json()["enabled"] is False
        assert client.post("/routines", json={"name": "x", "bot": "chief"}, headers=auth).status_code == 422
    store.close()


def test_the_starter_crew_comes_in_teams_and_a_card_can_change_its_own(home, tmp_path):
    teams = {name: bot.team for name, bot in crew.load(home).bots.items()}
    assert teams == {"chief": "Leads", "eng-lead": "Leads", "engineer-1": "Engineers", "engineer-2": "Engineers",
                     "engineer-3": "Engineers", "engineer-4": "Engineers", "reviewer": "Reviewers",
                     "critic": "Reviewers"}
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, crew.load(home), {"claude": FakeBackend(), "codex": FakeBackend()})
    auth = {"Authorization": "Bearer t"}
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        assert client.put("/bots/critic/card", json={"team": "Design"}, headers=auth).json()["team"] == "Design"
        assert {b["name"]: b["team"] for b in client.get("/bots", headers=auth).json()}["critic"] == "Design"
        client.put("/bots/critic/card", json={"team": ""}, headers=auth)
        assert runner.crew.bots["critic"].team is None and "team:" not in (home / "bots" / "critic.md").read_text()
        assert client.put("/bots/critic/card", json={"team": "x" * 41}, headers=auth).status_code == 422
    store.close()
