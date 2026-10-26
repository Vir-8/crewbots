"""The prompts, as files: every one loads and renders, a crew's own copy wins, and an edit counts at once."""

import os

import pytest

from crewbots import crew_tools, prompts, reviews


def test_every_prompt_loads_and_its_placeholders_are_filled():
    names = prompts.names()
    assert {"crew-tools", "delegation", "chat-setting", "seat-setting", "memory-guide", "briefing", "retry", "resume",
            "review/how-to-review", "review/line-comment", "review/reviewer", "review/lead"} <= set(names)
    assert {f"tools/{name}" for name in crew_tools.SPECS} <= set(names)  # every crew tool has its description
    values = {"title": "'Design'", "others": "critic", "lead": "You lead it.", "no_reply": "NO_REPLY", "error": "E",
              "request": "R", "how_to_review": "H", "line_comment": "L"}
    for name in names:
        text = prompts.text(name, **values)
        assert text.strip() and not text.endswith("\n"), name
        assert not any(f"{{{key}}}" in text for key in values), name


def test_the_review_prompts_include_how_to_review_and_how_to_comment():
    lead = reviews.review_prompt("lead")
    assert prompts.text("review/how-to-review") in lead and prompts.text("review/line-comment") in lead
    assert "{how_to_review}" not in reviews.review_prompt("reviewer")


def test_a_crews_own_copy_wins_and_deleting_it_brings_the_default_back(tmp_path):
    home = tmp_path / "crew"
    default = prompts.text("briefing", home)
    own = home / "prompts" / "briefing.md"
    own.parent.mkdir(parents=True)
    own.write_text("Write a briefing, in {words} words.\n")

    assert prompts.text("briefing", home, words=200) == "Write a briefing, in 200 words."
    assert prompts.path("briefing", home) == own
    own.unlink()
    assert prompts.text("briefing", home) == default


def test_an_edit_counts_from_the_next_read(tmp_path):
    home = tmp_path / "crew"
    own = home / "prompts" / "tools" / "recall.md"
    own.parent.mkdir(parents=True)
    own.write_text("Search the record.\n")
    assert crew_tools.describe("recall", home) == "Search the record."

    own.write_text("Search everything the crew said.\n")
    later = own.stat().st_mtime_ns + 1_000_000
    os.utime(own, ns=(later, later))  # a new modification time, as an editor's save gives it

    assert crew_tools.describe("recall", home) == "Search everything the crew said."
    assert [t.description for t in crew_tools.tools_for(False, home) if t.name == "recall"] == [
        "Search everything the crew said."]


def test_braces_that_arent_placeholders_are_left_alone(tmp_path):
    home = tmp_path / "crew"
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "retry.md").write_text('It failed: {error}. JSON looks like {"a": 1} and {unknown}.')

    assert prompts.text("retry", home, error="boom") == 'It failed: boom. JSON looks like {"a": 1} and {unknown}.'


def test_an_unknown_prompt_is_an_error():
    with pytest.raises(KeyError):
        prompts.text("no-such-prompt")
