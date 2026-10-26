"""A bot can add a bot to the crew when asked, with no more than it has itself, and can never remove or change one."""

import pytest
from fakes import FakeBackend

from crewbots import crew
from crewbots.crew_tools import TOOLS, CrewTools, ToolRefused
from crewbots.runner import Runner
from crewbots.store import Store

MARKETING = {"name": "marketing", "title": "Marketing Lead", "description": "Plans launches and writes the copy.",
             "instructions": "## Mission\n\nGet the game in front of players."}


@pytest.fixture
def crewed(tmp_path):
    home = crew.init(tmp_path / "crew")
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

    said = await chief.call("add_bot", {**MARKETING, "display_name": "Mira"})

    assert said.startswith("Added Mira (@marketing), Marketing Lead, to the crew.")
    marketing = runner.crew.bots["marketing"]
    assert marketing.access.value == "read_only" and marketing.backend == runner.crew.bots["chief"].backend
    assert (runner.crew.home / "bots" / "marketing.md").exists()
    assert any(e.kind == "notice" and "Added Mira" in e.data["message"] for e in store.events(task.id))


async def test_a_new_bot_gets_no_more_than_its_maker(crewed):
    runner, store = crewed
    engineer, _ = tools(runner, store, "engineer")  # writes in its folder, doesn't delegate
    chief, _ = tools(runner, store, "chief")  # reads only

    with pytest.raises(ToolRefused, match="more access than you"):
        await chief.call("add_bot", {**MARKETING, "access": "workspace_write"})
    with pytest.raises(ToolRefused, match="only a bot that delegates"):
        await engineer.call("add_bot", {**MARKETING, "can_delegate": True})
    with pytest.raises(ToolRefused, match="MCP servers you have yourself"):
        await engineer.call("add_bot", {**MARKETING, "mcp": ["blender"]})
    with pytest.raises(ToolRefused, match="more network than you"):
        await chief.call("add_bot", {**MARKETING, "network": True})
    assert "marketing" not in runner.crew.bots


async def test_a_taken_name_is_refused_and_no_tool_removes_or_changes_a_bot(crewed):
    runner, store = crewed
    chief, _ = tools(runner, store, "chief")

    with pytest.raises(ToolRefused, match="already a bot called engineer"):
        await chief.call("add_bot", {**MARKETING, "name": "engineer"})
    assert runner.crew.bots["engineer"].title == "Engineer"
    assert not [t.name for t in TOOLS if any(w in t.name for w in ("remove", "delete", "edit", "update", "change"))]


async def test_a_card_the_crew_couldnt_load_is_never_left_behind(crewed):
    runner, store = crewed
    chief, _ = tools(runner, store, "chief")

    with pytest.raises(ToolRefused, match="no skills named"):
        await chief.call("add_bot", {**MARKETING, "skills": ["nonexistent"]})
    assert not (runner.crew.home / "bots" / "marketing.md").exists()
