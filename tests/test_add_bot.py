"""The chief can add a bot to the crew when asked, with whatever it gives it, and no bot can remove or change one."""

import json

import pytest
from fakes import FakeBackend

from crewbots import cards, crew
from crewbots.crew_tools import SPECS, CrewTools, ToolRefused
from crewbots.runner import Runner
from crewbots.store import Store

MARKETING = {"name": "marketing", "title": "Marketing Lead", "description": "Plans launches and writes the copy.",
             "instructions": "## Mission\n\nGet the game in front of players."}


@pytest.fixture
def crewed(tmp_path):
    home = crew.init(tmp_path / "crew")
    (home / "mcp.json").write_text(json.dumps({"mcpServers": {"blender": {"command": "blender-mcp"},
                                                              "unity": {"type": "http", "url": "http://x/mcp"}}}))
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, crew.load(home), {"fake": FakeBackend()}, worktree_root=tmp_path / "wts")
    yield runner, store
    store.close()


def tools(runner, store, bot):
    task = store.create_task(bot, "asked to add a bot")
    return CrewTools(runner, task.id, can_delegate=runner.crew.bots[bot].can_delegate), task


async def test_the_chief_adds_a_bot_when_asked(crewed):
    runner, store = crewed
    chief, task = tools(runner, store, "chief")

    said = await chief.call("add_bot", {**MARKETING, "display_name": "Mira", "access": "workspace_write"})

    assert said.startswith("Added Mira (@marketing), Marketing Lead, to the crew.")
    marketing = runner.crew.bots["marketing"]
    assert marketing.access.value == "workspace_write" and marketing.backend == runner.crew.bots["chief"].backend
    assert marketing.mcp_names == ("blender", "unity")  # every server the crew has, unless the chief names some
    assert any(e.kind == "notice" and "Added Mira" in e.data["message"] for e in store.events(task.id))


async def test_adding_earlier_coordinator_does_not_change_chief(crewed):
    runner, store = crewed
    chief, _ = tools(runner, store, "chief")
    await chief.call("add_bot", {**MARKETING, "name": "aaa", "can_delegate": True})
    assert list(runner.crew.bots)[0] == "aaa"
    assert store.chief() == runner.coordinator().name == "chief"
    assert not runner.is_coordinator("aaa")


async def test_the_chief_gives_what_it_chooses_short_of_full_access_and_network_it_hasnt_got(crewed):
    runner, store = crewed
    chief, _ = tools(runner, store, "chief")

    with pytest.raises(ToolRefused, match="full access"):
        await chief.call("add_bot", {**MARKETING, "access": "full"})
    with pytest.raises(ToolRefused, match="you have no network"):
        await chief.call("add_bot", {**MARKETING, "network": True})

    cards.save_card(runner.crew.home, "chief", {"network": True}, None)
    runner.reload(crew.load(runner.crew.home))
    chief, _ = tools(runner, store, "chief")
    await chief.call("add_bot", {**MARKETING, "network": True, "can_delegate": True, "mcp": ["unity"]})
    added = runner.crew.bots["marketing"]
    assert added.network is True and added.can_delegate and added.mcp_names == ("unity",)


async def test_a_taken_name_is_refused_and_no_tool_removes_or_changes_a_bot(crewed):
    runner, store = crewed
    chief, _ = tools(runner, store, "chief")

    with pytest.raises(ToolRefused, match="already a bot called engineer-1"):
        await chief.call("add_bot", {**MARKETING, "name": "engineer-1"})
    assert runner.crew.bots["engineer-1"].title == "Engineer 1"
    changing = [name for name in SPECS if any(w in name for w in ("remove", "delete", "edit", "update", "change"))]
    assert changing == ["edit_page", "update_state", "open_unity_editor"]  # its pages, its state, an Editor: no bot


async def test_a_card_the_crew_couldnt_load_is_never_left_behind(crewed):
    runner, store = crewed
    chief, _ = tools(runner, store, "chief")

    with pytest.raises(ToolRefused, match="no skills named"):
        await chief.call("add_bot", {**MARKETING, "skills": ["nonexistent"]})
    assert not (runner.crew.home / "bots" / "marketing.md").exists()
