"""A bot's pages: its scratchpad and the pages it links to, Markdown files in its own folder that the user edits too."""

import pytest
from fakes import FakeBackend, ScriptedCrew, bot, make_crew
from fastapi.testclient import TestClient

from crewbots import pages, paths
from crewbots.api import create_app
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.pages import PageError
from crewbots.runner import Runner
from crewbots.store import Store

TOKEN, PORT = "t" * 32, 8765
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def test_pages_link_to_each_other_and_the_scratchpad_comes_first(tmp_path):
    folder = tmp_path / "chief"
    pages.write(folder, "Projects/Sky Islands", "# Sky Islands\n\nDemo on Friday.")
    pages.write(folder, "Scratchpad", "## Top of mind\n\n- The demo: [[Sky Islands]]")
    pages.edit(folder, "Scratchpad", "The demo", "Friday's demo")

    assert [p.name for p in pages.pages(folder)] == ["Scratchpad", "Projects/Sky Islands"]
    assert pages.read(folder, "Scratchpad") == "## Top of mind\n\n- Friday's demo: [[Sky Islands]]\n"
    assert pages.resolve(folder, "sky islands") == "Projects/Sky Islands"  # by its own name, wherever it is
    shown = pages.for_instructions(folder)
    assert shown.startswith("## Top of mind") and "Your other pages: [[Projects/Sky Islands]]." in shown
    assert pages.for_instructions(tmp_path / "new") == "(empty: you haven't started it yet)"


def test_a_page_stays_in_its_folder_and_an_edit_names_text_that_is_there_once(tmp_path):
    folder = tmp_path / "chief"
    pages.write(folder, "Scratchpad", "- a\n- a\n")
    for name in ("../elsewhere", "/etc/passwd", "~/.ssh/config", ""):
        with pytest.raises(PageError):
            pages.write(folder, name, "x")
    with pytest.raises(PageError, match="found 2 times"):
        pages.edit(folder, "Scratchpad", "- a", "- b")
    with pytest.raises(PageError, match="not found"):
        pages.edit(folder, "Scratchpad", "- c", "- b")
    with pytest.raises(PageError, match="no page named"):
        pages.read(folder, "Missing")


async def test_a_bot_keeps_its_scratchpad_and_has_it_in_view_in_its_next_session(store, tmp_path):
    crew = ScriptedCrew(store, tmp_path, bot("chief"))
    chat = crew.runner.chat("chief")
    tools = CrewTools(crew.runner, chat.id, can_delegate=False)

    said = await tools.call("write_page", {"page": "Scratchpad", "content": "## Waiting on you\n\n- The capsule art"})
    await tools.call("edit_page", {"page": "Scratchpad", "find": "capsule art", "replace": "store capsule art"})
    with pytest.raises(ToolRefused, match="not found"):
        await tools.call("edit_page", {"page": "Scratchpad", "find": "nothing like it", "replace": "x"})

    assert str(paths.bot_folders() / "chief" / "Scratchpad.md") in said
    instructions = crew.runner.crew.system_prompt(crew.runner.crew.bots["chief"])
    assert "## Scratchpad\n\n## Waiting on you\n\n- The store capsule art" in instructions


def test_the_user_reads_and_edits_a_bots_pages_from_the_app(store, tmp_path):
    runner = Runner(store, make_crew(tmp_path / "crew", bot("chief")), {"fake": FakeBackend()})
    pages.write(paths.bot_folders() / "chief", "Plans/Launch", "Ship it.")
    with TestClient(create_app(runner, TOKEN, PORT), base_url=f"http://127.0.0.1:{PORT}/v1") as client:
        put = client.put("/bots/chief/pages/Scratchpad", json={"text": "See [[Launch]]."}, headers=AUTH)
        assert put.status_code == 200 and put.json()["text"] == "See [[Launch]].\n"

        listed = client.get("/bots/chief/pages", headers=AUTH).json()
        assert [p["name"] for p in listed] == ["Scratchpad", "Plans/Launch"] and listed[0]["text"] is None
        assert client.get("/bots/chief/pages/Launch", headers=AUTH).json()["name"] == "Plans/Launch"  # by its link
        assert client.get("/bots/chief/pages/Nothing", headers=AUTH).status_code == 404
        assert client.put("/bots/chief/pages/..%2Fx", json={"text": "x"}, headers=AUTH).status_code in (404, 422)
        assert client.get("/bots/nobody/pages", headers=AUTH).status_code == 404
