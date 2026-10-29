"""Group chats a crew has from the start: made once from its rooms/ files, telling their bots what they're for, and made
again when the crew starts over."""

from datetime import datetime

import pytest
from fakes import FakeBackend

from crewbots import crew as crew_module
from crewbots import reset, room_files
from crewbots.runner import Runner
from crewbots.store import Status, Store

ENGINEERS = [f"engineer-{n}" for n in range(1, 5)]


@pytest.fixture
def home(tmp_path):
    return crew_module.init(tmp_path / "crew")


def runner_for(home):
    return Runner(Store(home / "crewbots.db"), crew_module.load(home), {"fake": FakeBackend()})


async def test_a_new_crew_has_its_engineering_group_chat_made_once(home):
    runner = runner_for(home)

    [room] = runner.make_standing_rooms()

    assert (room["title"], room["lead"], room["members"]) == (
        "Engineering", "eng-lead", ["eng-lead", *ENGINEERS, "reviewer", "critic"])
    assert runner.make_standing_rooms() == []
    await runner.delete_room(room["id"])
    assert runner.make_standing_rooms() == [] and runner.store.rooms() == []  # deleted stays deleted
    runner.store.close()


def test_its_bots_are_told_what_it_is_for(home):
    runner = runner_for(home)
    [room] = runner.make_standing_rooms()
    seat = runner.store.create_task("engineer-2", "Engineering", kind="seat", room_id=room["id"], status=Status.IDLE)

    setting = runner._setting(seat)

    assert "eng-lead leads it" in setting
    assert "\n\nWhat this group chat is for: Where engineering goals are planned, built, reviewed and merged." in setting
    runner.store.close()


def test_starting_over_makes_it_again(home):
    runner = runner_for(home)
    runner.make_standing_rooms()
    runner.store.close()

    reset.reset(home, datetime(2026, 10, 29, 12, 0))

    runner = runner_for(home)
    assert [r["title"] for r in runner.make_standing_rooms()] == ["Engineering"]
    runner.store.close()


@pytest.mark.parametrize(("frontmatter", "error"), [
    ("title: Art\nmembers: [chief, painter]", "no bots named ['painter']"),
    ("members: [chief, critic]\nlead: reviewer", "the lead, reviewer, isn't one of its members"),
    ("members: [chief]\ntopic: art", "unknown fields ['topic']"),
    ("title: Art", "members lists the bots"),
    ("members: [chief", "isn't valid YAML"),
])
def test_a_file_that_cant_be_made_is_left_out_and_says_why(home, frontmatter, error):
    (home / "rooms" / "art.md").write_text(f"---\n{frontmatter}\n---\nDrawing.\n")

    files, errors = room_files.load(home / "rooms", set(crew_module.load(home).bots))

    assert [f.name for f in files] == ["engineering"] and error in errors["art.md"]
