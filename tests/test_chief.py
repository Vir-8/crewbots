from datetime import datetime

import pytest
from fakes import bot, make_crew

from crewbots import crew, push
from crewbots.crew_tools import CrewTools, ToolRefused
from crewbots.projects import Project
from crewbots.runner import NotAllowed, Runner
from crewbots.store import Store


async def test_coordinator_migration_catalogs_and_server_refusals(tmp_path):
    store = Store(tmp_path / "crew.db")
    bots = make_crew(tmp_path, bot("chief", can_delegate=True), bot("eng-lead", can_delegate=True), bot("worker"))
    runner = Runner(store, bots, api_url="http://localhost/v1")
    assert store.coordinators() == ["chief", "eng-lead"]
    store.set_coordinators(["eng-lead"])
    runner = Runner(store, bots, api_url="http://localhost/v1")
    assert store.coordinators() == ["eng-lead"]  # migration runs once
    for name, coordinator, chief in (("chief", True, True), ("eng-lead", True, False), ("worker", False, False)):
        chat = runner.chat(name)
        tools = CrewTools(runner, chat.id, coordinator)
        catalog = {tool.name for tool in tools.definitions()}
        assert {"propose_merge", "withdraw_merge", "next_check"}.issubset(catalog) == coordinator
        assert {"add_bot", "propose_push"}.isdisjoint(catalog) != chief
        env = next(iter(runner._crew_tools("token", bots.bots[name]).values())).env
        assert (env["CREWBOTS_DELEGATE"] == "1", env["CREWBOTS_CHIEF"] == "1") == (coordinator, chief)
        prompt = bots.system_prompt(bots.bots[name], coordinator=coordinator, chief=chief)
        assert ("`propose_merge(" in prompt) == coordinator
        assert ("`add_bot(" in prompt) == chief
        if not chief:
            with pytest.raises(ToolRefused):
                await tools.call("add_bot", {})
            with pytest.raises(ToolRefused):
                await tools.call("propose_push", {})
            with pytest.raises(NotAllowed, match="only the chief"):
                runner.add_bot(chat.id, {})
            with pytest.raises(NotAllowed, match="only the chief"):
                await push.propose(runner, chat.id, "demo", "Ready")
        if not coordinator:
            with pytest.raises(NotAllowed):
                runner.next_check(chat.id, 10, "follow up")
    store.close()


def test_legacy_selection_is_saved_once_and_missing_chief_fails_closed(tmp_path):
    store = Store(tmp_path / "crew.db")
    crew = make_crew(tmp_path, bot("worker"), bot("old-chief", can_delegate=True))
    runner = Runner(store, crew)
    assert store.chief() == runner.coordinator().name == "old-chief"
    changed = make_crew(tmp_path, bot("a-helper", can_delegate=True), bot("worker"))
    runner = Runner(store, changed)
    assert store.chief() == "old-chief"
    assert runner.coordinator() is None
    assert not runner.is_coordinator("a-helper")
    assert not runner.always_on("a-helper")
    store.close()


def test_no_legacy_coordinator_stays_unset_when_bots_are_added(tmp_path):
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, make_crew(tmp_path, bot("first"), bot("second")))
    assert runner.coordinator() is None and store.chief() == ""
    runner = Runner(store, make_crew(tmp_path, bot("first"), bot("new", can_delegate=True)))
    assert runner.coordinator() is None and store.chief() == ""
    store.set_chief("001")  # the existing settings column has SQLite INTEGER affinity
    assert store.chief() == "001"
    store.close()


def test_migration_survives_restart_and_role_card_reordering(tmp_path):
    home = crew.init(tmp_path / "crew")
    db = tmp_path / "crew.db"
    store = Store(db)
    assert Runner(store, crew.load(home)).coordinator().name == "chief"
    store.close()
    (home / "bots" / "a-helper.md").write_text("---\nname: a-helper\ndescription: helper\ncan_delegate: true\n---\n")
    store = Store(db)
    runner = Runner(store, crew.load(home))
    assert list(runner.crew.bots)[0] == "a-helper"
    assert runner.coordinator().name == store.chief() == "chief"
    store.close()


def test_missing_named_chief_refuses_merge_and_chief_chat_routing(tmp_path):
    store = Store(tmp_path / "crew.db")
    Runner(store, make_crew(tmp_path, bot("chief", can_delegate=True), bot("aaa", can_delegate=True), bot("worker")))
    runner = Runner(store, make_crew(tmp_path, bot("aaa", can_delegate=True), bot("worker")))
    assert store.chief() == "chief" and runner.coordinator() is None
    store.set_coordinators([])
    chats = {name: runner.chat(name) for name in runner.crew.bots}
    for name, chat in chats.items():
        child = store.create_task("worker", "work", parent_id=chat.id, created_by=name)
        with pytest.raises(NotAllowed, match="only a coordinator"):
            runner.propose_merge(chat.id, child.id, "Ready")
        assert store.get_task(child.id).merge_state is None
        assert not store.last_events(child.id, ("merge_proposed",), 1)

    runner._chief_room_item({"title": "Room", "id": "r_test"}, "worker", "escalated", "task t_test")
    runner.attention.raise_("worker", "fyi", "News", "checkin")
    assert runner.greet() is None
    assert runner.attention.digest(datetime.now().astimezone()) is None
    assert store.proactive_waiting("fyi")
    assert all(not store.events(chat.id) for chat in chats.values())
    store.close()


async def test_missing_named_chief_refuses_push_for_every_bot(tmp_path):
    store = Store(tmp_path / "crew.db")
    Runner(store, make_crew(tmp_path, bot("chief", can_delegate=True), bot("aaa", can_delegate=True), bot("worker")))
    runner = Runner(store, make_crew(tmp_path, bot("aaa", can_delegate=True), bot("worker")))
    for name in runner.crew.bots:
        chat = runner.chat(name)
        with pytest.raises(NotAllowed, match="only the chief"):
            await push.propose(runner, chat.id, "demo", "Ready")
        assert not store.last_events(chat.id, ("push_proposed",), 1)
    store.close()


async def test_push_uses_selected_chief_even_without_legacy_flag(tmp_path, monkeypatch):
    store = Store(tmp_path / "crew.db")
    bots = make_crew(tmp_path, bot("chief", can_delegate=True), bot("aaa"))
    bots.projects["demo"] = Project("demo", tmp_path, "", tmp_path / "demo.md")
    runner = Runner(store, bots)
    store.set_chief("aaa")
    monkeypatch.setattr(push, "snapshot", lambda folder: {"commits": []})
    chat = runner.chat("aaa")
    event_id = await push.propose(runner, chat.id, "demo", "Ready")
    assert store.last_event(chat.id, ("push_proposed",)).id == event_id
    store.close()


def test_selected_chief_gets_push_prompt_and_tools_without_legacy_flag(tmp_path):
    store = Store(tmp_path / "crew.db")
    bots = make_crew(tmp_path, bot("old", can_delegate=True), bot("new"))
    runner = Runner(store, bots, api_url="http://localhost/v1")
    store.set_chief("new")
    old, new = bots.bots["old"], bots.bots["new"]
    for member, selected in ((old, False), (new, True)):
        chat = runner.chat(member.name)
        spec = runner._crew_tools("token", member)
        env = next(iter(spec.values())).env
        assert env["CREWBOTS_CHIEF"] == ("1" if selected else "0")
        assert env["CREWBOTS_DELEGATE"] == "1"
        tools = CrewTools(runner, chat.id, True)
        assert ("propose_push" in {tool.name for tool in tools.definitions()}) == selected
        prompt = bots.system_prompt(member, coordinator=runner.is_coordinator(member.name), chief=selected)
        assert (crew.prompts.text("chief-push", bots.home) in prompt) == selected
        assert crew.prompts.text("coordinating", bots.home) in prompt
    store.close()
