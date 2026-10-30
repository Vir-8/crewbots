"""Files a bot shows the user: ones it shares, copied into the crew's attachments and shown under what it says, from
its own folders only."""

import os

import pytest
from fakes import ScriptedCrew, bot, wait_until
from test_bot_api import ADMIN, as_bot, crew_app, start  # noqa: F401

from crewbots import paths
from crewbots.backends import Access
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.events import Done
from crewbots.store import Store

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


@pytest.fixture
def studio(tmp_path):
    """The artist's own folder: outside the crew's, where a writing bot can't work."""
    folder = tmp_path.parent / f"{tmp_path.name}-studio"
    folder.mkdir()
    return folder


@pytest.fixture
def crew(store, tmp_path, studio):
    scripted = ScriptedCrew(store, tmp_path, bot("artist", Access.WORKSPACE_WRITE, workspace=studio), bot("critic"))
    scripted.script["artist"] = scripted.script["critic"] = lambda spec, me: [Done(final_text="ok")]
    return scripted


async def test_a_shared_file_is_a_copy_shown_under_the_bots_message(crew, store, tmp_path, studio):
    (studio / "renders").mkdir()
    (studio / "renders" / "shot.png").write_bytes(PNG)
    chat = crew.runner.chat("artist")
    said = await CrewTools(crew.runner, chat.id, can_delegate=False).call(
        "share", {"files": ["renders/shot.png"], "text": "The first pass."})
    assert said.startswith("Shared shot.png with the user")

    [shared] = [e for e in store.events(chat.id) if e.kind == "shared"]
    [copy] = shared.data["attachments"]
    assert shared.data["text"] == "The first pass." and copy["kind"] == "image" and copy["mime"] == "image/png"
    assert copy["path"].startswith(str(tmp_path / "attachments"))
    (studio / "renders" / "shot.png").write_bytes(b"changed")  # the bot carries on; what it showed stays
    assert open(copy["path"], "rb").read() == PNG
    assert [h["task_id"] for h in store.search("pass")] == [chat.id]  # found by what was said with it


async def test_a_file_in_the_claude_sandboxs_temp_folder_is_the_bots_own(crew, tmp_path, monkeypatch):
    temp = tmp_path.parent / f"{tmp_path.name}-claude"  # where Claude's sandbox points TMPDIR: verify-unity's shots
    (temp / "lh-verify.aBWuyF").mkdir(parents=True)
    (temp / "lh-verify.aBWuyF" / "door.png").write_bytes(PNG)
    monkeypatch.setattr("crewbots.runner.files.SANDBOX_TEMP", temp)
    said = await CrewTools(crew.runner, crew.runner.chat("artist").id, can_delegate=False).call(
        "share", {"files": str(temp / "lh-verify.aBWuyF" / "door.png")})
    assert said.startswith("Shared door.png with the user")


async def test_only_files_in_the_bots_own_folders(crew, store, tmp_path, studio):
    (tmp_path / "secrets.txt").write_text("not for sharing")
    (studio / "link.txt").symlink_to(tmp_path / "secrets.txt")
    chat = crew.runner.chat("artist")
    tools = CrewTools(crew.runner, chat.id, can_delegate=False)
    for path in (str(tmp_path / "secrets.txt"), "../secrets.txt", "link.txt", "~/.ssh/id_ed25519"):
        with pytest.raises(ToolRefused, match="isn't in your folders"):
            await tools.call("share", {"files": [path]})
    assert store.agent_log()[0]["kind"] == "refused"
    with pytest.raises(ToolRefused, match="isn't a file"):
        await tools.call("share", {"files": ["missing.png"]})
    with pytest.raises(ToolRefused, match="at most 10"):
        await tools.call("share", {"files": [f"{n}.png" for n in range(11)]})
    assert not (tmp_path / "attachments").exists()  # nothing copied for a share that was refused

    own = paths.bot_folders() / "critic"  # a bot with no folder named on its card shares from its own folder
    own.mkdir(parents=True)
    (own / "notes.md").write_text("# Notes")
    said = await CrewTools(crew.runner, crew.runner.chat("critic").id, False).call("share", {"files": ["notes.md"]})
    assert "notes.md" in said


async def test_in_a_group_chat_shared_files_are_posted_there(crew, store, tmp_path, studio):
    (studio / "cut.mp4").write_bytes(b"\0" * 2048)
    room = crew.runner.create_room("Launch video", ["artist", "critic"], "artist")
    crew.runner.post(room["id"], "user", "show us the cut")
    await wait_until(lambda: store.seat_of(room["id"], "artist") is not None)
    seat = store.seat_of(room["id"], "artist")
    await CrewTools(crew.runner, seat.id, can_delegate=False).call("share", {"files": ["cut.mp4"], "text": "@critic thoughts?"})
    posted = [m for m in store.room_messages(room["id"]) if m["attachments"]]
    assert [(m["author"], m["text"], m["attachments"][0]["mime"]) for m in posted] == [
        ("artist", "@critic thoughts?", "video/mp4")]
    await wait_until(lambda: store.seat_of(room["id"], "critic") is not None)  # it woke the teammate it mentioned
    await wait_until(lambda: not crew.runner.running())

    said = await CrewTools(crew.runner, crew.runner.chat("artist").id, False).call(
        "share", {"files": ["cut.mp4"], "room": room["id"]})
    assert f"in the group chat ({room['id']})" in said


def test_codex_bots_share_through_the_bot_api(crew_app, tmp_path):  # noqa: F811
    client, backend, store = crew_app
    (tmp_path / "chart.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    task_id, token = start(client, backend, "engineer")
    store.update_task(task_id, workspace=str(tmp_path))
    shared = client.post("/bot/share", json={"files": ["chart.svg"]}, headers=as_bot(token))
    assert shared.status_code == 201 and shared.json()["files"][0]["mime"] == "image/svg+xml"
    assert client.post("/bot/share", json={"files": ["nope.svg"]}, headers=as_bot(token)).status_code == 422
    assert client.post("/bot/share", json={"files": ["/etc/hosts"]}, headers=as_bot(token)).status_code == 403


async def test_the_chat_list_names_files_shared_without_words(crew, store, tmp_path, studio):
    from fastapi.testclient import TestClient

    from crewbots.api import create_app

    (studio / "cut.mp4").write_bytes(b"\0" * 64)
    await CrewTools(crew.runner, crew.runner.chat("artist").id, False).call("share", {"files": ["cut.mp4"]})
    with TestClient(create_app(crew.runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as client:
        [chat] = [c for c in client.get("/chats", headers={"Authorization": "Bearer t"}).json() if c["bot"] == "artist"]
    assert chat["last"]["text"] == "cut.mp4" and chat["unread"] == 1


def test_images_from_tools_come_apart_from_their_text():
    import base64

    from crewbots.backends.claude_cli import ClaudeParser
    from crewbots.backends.codex_app_server import AppServerParser

    png = base64.b64encode(PNG).decode()
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png}}
    read = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [image]}]}}
    [result] = ClaudeParser().feed(read)
    assert result.output == "" and result.images == [{"mime": "image/png", "data": png}]  # no base64 as text

    codex = AppServerParser()
    viewed = codex.feed("item/completed", {"item": {"type": "imageView", "id": "v1", "path": "/w/shot.png"}})
    assert [(e.kind, getattr(e, "name", None), getattr(e, "images", None)) for e in viewed] == [
        ("tool_call", "view_image", None), ("tool_result", None, [{"path": "/w/shot.png"}])]
    mcp = {"type": "mcpToolCall", "id": "m1", "server": "blender", "tool": "get_screenshot", "arguments": {},
           "status": "completed", "result": {"content": [{"type": "text", "text": "the viewport"},
                                                         {"type": "image", "data": png, "mimeType": "image/png"}]}}
    *_, shot = codex.feed("item/completed", {"item": mcp})
    assert shot.output == "the viewport" and shot.images == [{"mime": "image/png", "data": png}]
    made = {"type": "imageGeneration", "id": "g1", "status": "completed", "revisedPrompt": "a logo",
            "savedPath": "/w/logo.png"}
    call, result = codex.feed("item/completed", {"item": made})
    assert call.name == "image_generation" and result.images == [{"path": "/w/logo.png"}]


async def test_a_turns_tool_images_are_kept_as_files_once_each(store, tmp_path):
    import base64

    from crewbots.events import ToolCall, ToolResult

    (tmp_path / "seen.png").write_bytes(PNG + b"seen")
    png = base64.b64encode(PNG).decode()
    crew = ScriptedCrew(store, tmp_path, bot("artist"))
    crew.script["artist"] = lambda spec, me: [
        ToolCall(id="a", name="Read", input={"file_path": "renders/hero.png"}),
        ToolResult(id="a", output="", images=[{"mime": "image/png", "data": png}, {"mime": "image/png", "data": png}]),
        ToolResult(id="b", output="", images=[{"path": str(tmp_path / "seen.png")}, {"path": str(tmp_path / "gone.png")},
                                              {"mime": "image/png", "data": "not base64!"}]),
        ToolResult(id="c", output="no pictures"), Done(final_text="ok")]
    task = crew.runner.say("artist", "screenshot it")
    await wait_until(lambda: not crew.runner.running())
    first, second, none = [e.data.get("images") for e in store.events(task.id) if e.kind == "tool_result"]
    assert none is None  # nothing stored for the many results with no images
    assert [i["id"] for i in first][0] == [i["id"] for i in first][1]  # the same picture, kept once
    assert [i["name"] for i in second] == ["seen.png"] and first[0]["name"] == "hero.png"
    assert open(first[0]["path"], "rb").read() == PNG
    raw = store.db.execute("select group_concat(data) from events where kind = 'tool_result'").fetchone()[0]
    assert png not in raw  # the database holds where they are, not the pictures


async def test_files_a_reply_names_show_under_it_where_they_are(crew, store, tmp_path, studio):
    from crewbots.events import Text

    (studio / "renders").mkdir()
    (studio / "renders" / "hero.png").write_bytes(PNG)
    (studio / "README.md").write_text("old news")  # a text file it didn't touch this turn
    os.utime(studio / "README.md", (1e9, 1e9))
    (studio / "app.py").write_text("print()")  # code: the diff shows it
    (tmp_path / "elsewhere.pdf").write_bytes(b"%PDF")  # not in its folders

    def turn(spec, me):
        (studio / "notes.md").write_text("# Today")  # written in this turn
        (studio / "cut.mp4").write_bytes(b"\0" * 32)
        return [Text(text="Rendered `renders/hero.png` and [the notes](notes.md); see README.md, app.py and "
                          f"{tmp_path / 'elsewhere.pdf'}, and renders/missing.png."),
                Text(text="Nothing named here."), Done(final_text="ok")]

    crew.script["artist"] = turn
    chat = crew.runner.say("artist", "render it")
    await wait_until(lambda: not crew.runner.running())
    said = [e.data for e in store.events(chat.id) if e.kind == "text"]
    assert [(f["name"], f["id"], f["mime"]) for f in said[0]["files"]] == [
        ("hero.png", "", "image/png"), ("notes.md", "", "text/markdown")]
    assert said[0]["files"][0]["path"] == str((studio / "renders" / "hero.png").resolve())  # where it is, not a copy
    assert "files" not in said[1]

    named = crew.runner._mentioned(store.get_task(chat.id), studio, "Here's `cut.mp4`.", 0)
    assert [f["name"] for f in named] == ["cut.mp4"]
    await CrewTools(crew.runner, chat.id, can_delegate=False).call("share", {"files": ["cut.mp4"]})
    assert crew.runner._mentioned(store.get_task(chat.id), studio, "Here's `cut.mp4`.", 0) == []  # shared: not again


async def test_a_group_chat_reply_carries_the_files_it_names(crew, store, tmp_path, studio):
    from crewbots.events import Text

    (studio / "poster.pdf").write_bytes(b"%PDF-1.4")
    crew.script["artist"] = lambda spec, me: [Text(text="The poster: poster.pdf"), Done(final_text="The poster: poster.pdf")]
    room = crew.runner.create_room("Launch", ["artist", "critic"], "artist")
    crew.runner.post(room["id"], "user", "where's the poster?")
    await wait_until(lambda: any(m["author"] == "artist" for m in store.room_messages(room["id"])))
    await wait_until(lambda: not crew.runner.running())
    [reply] = [m for m in store.room_messages(room["id"]) if m["author"] == "artist"]
    assert [f["name"] for f in reply["attachments"]] == ["poster.pdf"]
