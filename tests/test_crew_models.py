"""Changing models across the crew: several bots in one call, all or nothing."""

import pytest
from fakes import FakeBackend
from fastapi.testclient import TestClient

from crewbots import crew
from crewbots import models as models_module
from crewbots.api import create_app
from crewbots.runner import Runner
from crewbots.store import Store

ENGINEERS = [f"engineer-{n}" for n in range(1, 5)]
LISTED = {
    "claude": [{"id": "default", "efforts": ["low", "medium", "high"], "default": True},
               {"id": "sonnet", "efforts": ["low", "medium", "high", "max"], "default": False},
               {"id": "haiku", "efforts": [], "default": False}],
    "codex": [{"id": "gpt-6", "efforts": ["low", "medium", "high", "xhigh"], "default": True},
              {"id": "gpt-6-mini", "efforts": ["low", "medium"], "default": False}],
}


@pytest.fixture
def home(tmp_path):
    return crew.init(tmp_path / "crew")


@pytest.fixture
def api(home, tmp_path, monkeypatch):
    """The daemon's API on the starter crew, with the CLIs offering LISTED: (client, runner)."""
    async def listed(fresh=False):
        return LISTED

    monkeypatch.setattr(models_module, "models", listed)
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, crew.load(home), {"claude": FakeBackend(), "codex": FakeBackend()})
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765/v1",
                    headers={"Authorization": "Bearer t"}) as client:
        yield client, runner
    store.close()


def cards_of(home):
    return {p.name: p.read_text() for p in sorted((home / "bots").glob("*.md"))}


def test_one_call_changes_several_bots(api, home):
    client, runner = api
    change = {"backend": "claude", "model": "sonnet", "effort": "high"}

    done = client.put("/bots/models", json={"bots": {**{e: change for e in ENGINEERS}, "critic": {"team": "Design"}}})

    assert done.status_code == 200 and sorted(done.json()["changed"]) == sorted([*ENGINEERS, "critic"])
    bots = runner.crew.bots
    assert all((bots[e].backend, bots[e].model, bots[e].effort) == ("claude", "sonnet", "high") for e in ENGINEERS)
    assert bots["critic"].team == "Design" and bots["critic"].backend == "codex"
    assert "model: sonnet" in (home / "bots" / "engineer-1.md").read_text()
    assert client.put("/bots/models", json={"bots": {"critic": {"team": "Design"}}}).json()["changed"] == []


def test_one_bad_change_writes_nothing(api, home):
    client, runner = api
    before = cards_of(home)

    refused = client.put("/bots/models", json={"bots": {"engineer-1": {"model": "gpt-6-mini"},
                                                         "critic": {"effort": "enormous"}}})

    assert refused.status_code == 422 and "effort" in refused.json()["detail"]
    assert client.put("/bots/models", json={"bots": {"engineer-1": {"model": "gpt-6"},
                                                     "nobody": {"model": "x"}}}).status_code == 422
    assert cards_of(home) == before and runner.crew.bots["engineer-1"].model is None


def test_a_crew_that_fails_to_load_after_the_writes_is_put_back(api, home):
    client, runner = api
    reviewer = home / "bots" / "reviewer.md"  # a Claude bot with an SSE server, which Codex can't use
    reviewer.write_text(reviewer.read_text().replace(
        "team: Reviewers\n", "team: Reviewers\nmcp:\n  docs: {url: 'http://127.0.0.1:9/sse', type: sse}\n"))
    client.post("/crew/reload")
    before = cards_of(home)

    refused = client.put("/bots/models", json={"bots": {"engineer-1": {"model": "gpt-6-mini"},
                                                         "reviewer": {"backend": "codex"}}})

    assert refused.status_code == 422 and "SSE" in refused.json()["detail"]
    assert cards_of(home) == before and runner.crew.bots["reviewer"].backend == "claude"


def test_a_new_model_keeps_the_effort_only_if_it_offers_it(api, home):
    client, runner = api
    client.put("/bots/models", json={"bots": {e: {"effort": "high"} for e in ENGINEERS}})

    client.put("/bots/models", json={"bots": {"engineer-1": {"model": "gpt-6-mini"}, "engineer-2": {"model": "gpt-6"}}})

    bots = runner.crew.bots
    assert (bots["engineer-1"].model, bots["engineer-1"].effort) == ("gpt-6-mini", None)
    assert (bots["engineer-2"].model, bots["engineer-2"].effort) == ("gpt-6", "high")


def test_a_move_to_another_cli_starts_from_its_defaults(api, home):
    client, runner = api
    client.put("/bots/models", json={"bots": {"engineer-1": {"model": "gpt-6", "effort": "xhigh"}}})
    client.put("/bots/engineer-1/card", json={"access": "full"})

    client.put("/bots/models", json={"bots": {"engineer-1": {"backend": "antigravity"}}})

    bot = runner.crew.bots["engineer-1"]
    assert (bot.backend, bot.model, bot.effort, bot.access) == ("antigravity", None, None, "workspace_write")
