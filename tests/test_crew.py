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

    assert list(loaded.bots) == ["chief", "critic", "engineer", "reviewer"]
    assert loaded.bots["engineer"].backend == "codex"
    assert loaded.bots["engineer"].access is Access.WORKSPACE_WRITE
    assert loaded.bots["chief"].access is Access.READ_ONLY
    assert [b.name for b in loaded.bots.values() if b.can_delegate] == ["chief"]
    assert {loaded.bots["reviewer"].backend, loaded.bots["critic"].backend} == {"claude", "codex"}
    assert set(loaded.team_files) == {"mission.md", "standards.md", "protocol.md", "rules.md"}
    assert set(loaded.skills) == {"interrogate", "arena", "swarm"}
    assert loaded.bots["chief"].skills == ("interrogate", "arena", "swarm")


def test_init_never_overwrites_a_crew(tmp_path):
    home = crew.init(tmp_path / "crew")

    with pytest.raises(CrewError, match="already has a crew"):
        crew.init(home)


def test_every_bot_sees_its_card_the_roster_and_the_team_files(tmp_path):
    loaded = crew.load(crew.init(tmp_path / "crew"))

    prompt = loaded.system_prompt(loaded.bots["reviewer"])

    assert prompt.startswith("You are reviewer, the Reviewer")
    assert "Catch what the author missed" in prompt
    assert "- reviewer (@reviewer, Reviewer) ← you:" in prompt
    assert "- chief (@chief, Chief of Staff):" in prompt and "← you" not in prompt.split("- chief")[1].splitlines()[0]
    assert "# team/protocol.md" in prompt and "HANDOFF" in prompt


def test_a_bots_instructions_start_the_same_for_every_task_so_the_prompt_cache_matches(tmp_path):
    loaded = crew.load(crew.init(tmp_path / "crew"))
    chief = loaded.bots["chief"]

    from_user = loaded.system_prompt(chief)
    by_routine = loaded.system_prompt(chief, routine="standup", briefing="where we are")
    loaded.memory.remember("chief", "Vir prefers short replies", "Keep it brief.", source="t_1")
    with_a_note = loaded.system_prompt(chief)

    same = next(i for i, (a, b) in enumerate(zip(from_user, by_routine, strict=False)) if a != b)
    assert "No notes yet.\n\n# This task\n\n" in from_user[:same]  # all that stays put comes first
    assert "# team/protocol.md" in from_user[:same] and "# Skills" in from_user[:same]
    noted = next(i for i, (a, b) in enumerate(zip(from_user, with_a_note, strict=False)) if a != b)
    assert from_user.index("# Memory") < noted  # a new note changes only the end


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


def test_only_coordinators_are_told_they_can_delegate(tmp_path):
    loaded = crew.load(crew.init(tmp_path / "crew"))

    chief = loaded.system_prompt(loaded.bots["chief"])
    engineer = loaded.system_prompt(loaded.bots["engineer"], delegated_by="chief")

    assert "`delegate(bot, goal" in chief and "`delegate(bot, goal" not in engineer
    assert "`handoff(" in chief and "`handoff(" in engineer
    assert "This task comes from the user" in chief
    assert "delegated to you by chief" in engineer


def test_skills_are_listed_with_their_paths_for_bots_that_have_them(tmp_path):
    loaded = crew.load(crew.init(tmp_path / "crew"))

    chief = loaded.system_prompt(loaded.bots["chief"])

    assert "# Skills" in chief and str(loaded.home / "skills" / "arena" / "SKILL.md") in chief
    assert "# Skills" in loaded.system_prompt(loaded.bots["engineer"])  # every bot, unless its card says otherwise
    quiet = crew.Crew(home=loaded.home, bots={}, team_files={}, skills=loaded.skills)
    assert quiet.skills_for(parse_card("---\ndescription: d\nskills: none\n---\nbody", "x")) == []


def test_global_skills_are_there_for_every_bot_after_the_crews_own(tmp_path, monkeypatch):
    home = crew.init(tmp_path / "crew")
    mine = tmp_path / "global"
    write_skill(mine / "pdf", "pdf", "reads and fills PDFs")
    write_skill(mine / "synced" / "a1b2" / "xlsx", "xlsx", "works with spreadsheets")
    write_skill(mine / "arena", "arena", "a global one named like the crew's")
    write_skill(mine / ".system" / "imagegen", "imagegen", "a CLI's built-in")
    (mine / "broken").mkdir()
    (mine / "broken" / "SKILL.md").write_text("no frontmatter here")
    monkeypatch.setattr(crew, "GLOBAL_SKILL_DIRS", (mine,))

    loaded = crew.load(home)

    assert {"pdf", "xlsx"} <= set(loaded.skills) and not {"broken", "imagegen"} & set(loaded.skills)
    assert loaded.skills["arena"].source == "crew" and loaded.skills["pdf"].source == str(mine)
    assert loaded.skills["pdf"] in loaded.skills_for(loaded.bots["engineer"])
    assert mine in loaded.skill_dirs  # so a Claude bot may read them
    assert [(s.name, s.source) for s in loaded.shadowed_skills] == [("arena", str(mine))]  # listed, not used


def write_skill(folder: Path, name: str, description: str = "does a thing") -> None:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n# {name}\n")


def test_a_pack_keeps_its_layout_and_is_namespaced(tmp_path):
    home = crew.init(tmp_path / "crew")
    pack = tmp_path / "claude-code"
    write_skill(pack / "skills" / "arena", "arena")
    write_skill(pack / "skills" / "tdd", "tdd", "test first")
    (pack / "docs").mkdir()
    (pack / "docs" / "adapter.md").write_text("linked from the skills")

    added = crew.add_skills(home, pack, name="pstack")

    assert added == ["pstack:arena", "pstack:tdd"]
    assert (home / "skills" / "pstack" / "docs" / "adapter.md").exists()
    assert {"arena", "pstack:arena"} <= set(crew.load(home).skills)  # no clash with the crew's own arena


def test_a_single_skill_folder_keeps_its_plain_name(tmp_path):
    home = crew.init(tmp_path / "crew")
    write_skill(tmp_path / "standup", "standup")

    assert crew.add_skills(home, tmp_path / "standup") == ["standup"]


def test_adding_a_broken_skill_leaves_the_crew_as_it_was(tmp_path):
    home = crew.init(tmp_path / "crew")
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_text("---\nname: bad\n---\nno description\n")

    with pytest.raises(CrewError, match="needs a description"):
        crew.add_skills(home, tmp_path / "bad")
    assert not (home / "skills" / "bad").exists()


def test_a_card_naming_a_missing_skill_is_refused(tmp_path):
    home = crew.init(tmp_path / "crew")
    (home / "bots" / "writer.md").write_text("---\ndescription: writes\nskills: [nope]\n---\nhi\n")

    with pytest.raises(CrewError, match="no skills named"):
        crew.load(home)


def test_the_crewbots_mcp_name_is_reserved():
    with pytest.raises(CrewError, match="reserved"):
        parse_card("---\ndescription: x\nmcp:\n  crewbots:\n    command: evil\n---\n", default_name="x")


def test_network_on_a_role_card():
    def card(extra, backend="claude", access="workspace_write"):
        return parse_card(f"---\ndescription: d\nbackend: {backend}\naccess: {access}\n{extra}---\nbody", "x")

    assert card("").network is False
    assert card("network: true\n", backend="codex").network is True
    assert card("network: [pypi.org, '*.npmjs.org']\n").network == ("pypi.org", "*.npmjs.org")
    with pytest.raises(CrewError, match="Codex's sandbox can't keep to a list"):
        card("network: [pypi.org]\n", backend="codex")
    assert card("network: true\n", access="read_only").network is True  # web search and pages, for any bot
    with pytest.raises(CrewError, match="list of hosts"):
        card("network: everywhere\n")



def test_a_bot_can_have_a_name_and_a_face():
    card = "---\ndescription: d\ndisplay_name: Roger\navatar: {color: orange, shape: drop}\n---\nbody"

    bot = parse_card(card, "chief")
    assert bot.label == "Roger" and bot.name == "chief" and bot.avatar == {"color": "orange", "shape": "drop"}
    with pytest.raises(CrewError, match="avatar is"):
        parse_card("---\ndescription: d\navatar: {color: plaid}\n---\nbody", "x")


def test_a_bots_context_window():
    card = lambda value: f"---\ndescription: d\n{value}---\nbody"  # noqa: E731
    assert parse_card(card(""), "x").context_window == 200_000  # explicit, never the model's whole window
    assert parse_card(card("context_window: 400k\n"), "x").context_window == 400_000
    assert parse_card(card("context_window: 1m\n"), "x").context_window == 1_000_000
    assert parse_card(card("context_window: 300000\n"), "x").context_window == 300_000
    assert parse_card(card("context_window: auto\n"), "x").context_window is None
    for bad in ("50k", "2m", "lots"):
        with pytest.raises(CrewError, match="context_window"):
            parse_card(card(f"context_window: {bad}\n"), "x")


def test_a_bots_reasoning_effort():
    assert parse_card("---\ndescription: d\neffort: xhigh\n---\nbody", "x").effort == "xhigh"
    with pytest.raises(CrewError, match="effort is one of"):
        parse_card("---\ndescription: d\neffort: lots\n---\nbody", "x")
