from pathlib import Path

import pytest

from crewbots import crew
from crewbots.backends import Access, McpServer
from crewbots.crew import CrewError, parse_card

CARD = """---
title: Video Editor
description: Cuts gameplay footage into shorts in DaVinci Resolve.
backend: claude
model: sonnet
access: workspace_write
workspace: ~/videos
worktree: false
timeout: 600
mcp:
  resolve:
    command: uvx
    args: [davinci-resolve-mcp]
    env: {RESOLVE_PORT: 9000}
---
## Mission

Edit footage.
"""


def test_a_role_card_becomes_a_bot():
    bot = parse_card(CARD, default_name="video-editor")

    assert bot.name == "video-editor"
    assert bot.title == "Video Editor"
    assert bot.access is Access.WORKSPACE_WRITE
    assert bot.workspace == Path.home() / "videos"
    assert bot.worktree is False and bot.timeout_s == 600
    assert bot.mcp == {"resolve": McpServer(command="uvx", args=["davinci-resolve-mcp"], env={"RESOLVE_PORT": "9000"})}
    assert bot.instructions.startswith("## Mission")


@pytest.mark.parametrize(("change", "message"), [
    (("access: workspace_write", "access: root"), "access must be"),
    (("backend: claude", "backend: gpt"), "backend must be"),
    (("model: sonnet", "colour: blue"), "unknown fields"),
    (("description: Cuts gameplay footage into shorts in DaVinci Resolve.\n", ""), "description is required"),
    (("command: uvx", "cmd: uvx"), "needs a command"),
])
def test_mistakes_in_a_card_are_explained(change, message):
    with pytest.raises(CrewError, match=message):
        parse_card(CARD.replace(*change), default_name="x")


def test_a_card_without_frontmatter_is_refused():
    with pytest.raises(CrewError, match="frontmatter"):
        parse_card("just text", default_name="x")
    with pytest.raises(CrewError, match="closing"):
        parse_card("---\ntitle: x\n", default_name="x")


def test_the_starter_crew_loads(tmp_path):
    home = crew.init(tmp_path / "crew")

    loaded = crew.load(home)

    assert list(loaded.bots) == ["chief", "engineer", "reviewer"]
    assert loaded.bots["engineer"].backend == "codex"
    assert loaded.bots["engineer"].access is Access.WORKSPACE_WRITE
    assert loaded.bots["chief"].access is Access.READ_ONLY
    assert set(loaded.team_files) == {"mission.md", "standards.md", "protocol.md", "rules.md"}


def test_init_never_overwrites_a_crew(tmp_path):
    home = crew.init(tmp_path / "crew")

    with pytest.raises(CrewError, match="already has a crew"):
        crew.init(home)


def test_every_bot_sees_its_card_the_roster_and_the_team_files(tmp_path):
    loaded = crew.load(crew.init(tmp_path / "crew"))

    prompt = loaded.system_prompt(loaded.bots["reviewer"])

    assert prompt.startswith("You are reviewer, the Reviewer")
    assert "Catch what the author missed" in prompt
    assert "- reviewer (Reviewer) ← you:" in prompt
    assert "- chief (Chief of Staff):" in prompt and "← you" not in prompt.split("- chief")[1].splitlines()[0]
    assert "# team/protocol.md" in prompt and "HANDOFF" in prompt


def test_two_cards_with_the_same_name_are_refused(tmp_path):
    home = crew.init(tmp_path / "crew")
    (home / "bots" / "boss.md").write_text("---\nname: chief\ndescription: another chief\n---\nhi\n")

    with pytest.raises(CrewError, match="already exists"):
        crew.load(home)


def test_the_example_cards_in_the_docs_are_valid():
    for path in (Path(__file__).parent.parent / "docs" / "examples").glob("*.md"):
        assert parse_card(path.read_text(), default_name=path.stem, source=path).description


def test_a_folder_without_bots_is_not_a_crew(tmp_path):
    with pytest.raises(CrewError, match="crewbots init"):
        crew.load(tmp_path)
