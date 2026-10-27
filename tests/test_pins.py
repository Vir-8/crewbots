"""Chats pinned to the top of the sidebar, in the user's order, kept by the daemon so a reinstalled app keeps them."""

import pytest
from fakes import FakeBackend, bot, make_crew
from fastapi.testclient import TestClient

from crewbots.api import create_app
from crewbots.runner import Runner
from crewbots.store import Store

AUTH = {"Authorization": "Bearer t"}


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("chief", can_delegate=True), bot("engineer")), {"fake": FakeBackend()})
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765/v1") as c:
        yield c, store, runner
    store.close()


def test_pinned_chats_come_back_in_order_with_their_places(client):
    c, store, runner = client
    room = c.post("/rooms", json={"title": "Launch", "members": ["chief", "engineer"]}, headers=AUTH).json()
    with runner.bus.subscribe() as heard:
        pinned = c.put("/pins", json={"pins": [f"room:{room['id']}", "bot:engineer", "bot:engineer"]}, headers=AUTH)
        assert pinned.json() == {"pins": [f"room:{room['id']}", "bot:engineer"]}  # once each
        assert heard.get_nowait() == {"type": "pins", "pins": [f"room:{room['id']}", "bot:engineer"]}
    places = {chat["key"]: chat["pinned"] for chat in c.get("/chats", headers=AUTH).json()}
    assert places[f"room:{room['id']}"] == 0 and places["bot:engineer"] == 1 and places["bot:chief"] is None
    assert c.get("/pins", headers=AUTH).json() == {"pins": [f"room:{room['id']}", "bot:engineer"]}

    assert c.put("/pins", json={"pins": ["bot:engineer"]}, headers=AUTH).json() == {"pins": ["bot:engineer"]}  # unpinned
    assert c.put("/pins", json={"pins": ["page:settings"]}, headers=AUTH).status_code == 422
    c.put("/pins", json={"pins": [f"room:{room['id']}", "bot:engineer"]}, headers=AUTH)
    c.delete(f"/rooms/{room['id']}", headers=AUTH)
    assert store.pins() == ["bot:engineer"]  # a deleted group chat's pin goes with it
