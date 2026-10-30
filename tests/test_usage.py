"""The usage page's numbers: the crew's own use, and where the plans' limits stand."""

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fakes import bot

from crewbots import models, plans
from crewbots.plans import parse_claude_usage, parse_codex_limits
from crewbots.store import Store
from crewbots.usage import _streaks, summary

USAGE = """You are currently using your subscription to power your Claude Code usage

Current session: 5% used · resets Oct 26 at 5:40pm (Asia/Calcutta)
Current week (Fable): 0% used · resets Oct 28 at 11:30pm (Asia/Calcutta)
Current week (all models): 12% used · resets Oct 28 at 11:30pm (Asia/Calcutta)

What's contributing to your limits usage?
"""


def test_claudes_usage_command_becomes_windows_with_their_resets():
    now = datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    windows = parse_claude_usage(USAGE, now)

    assert [(w["name"], w["used_percent"]) for w in windows] == [
        ("Session", 5.0), ("Week · Fable", 0.0), ("Week · all models", 12.0)]
    assert datetime.fromtimestamp(windows[0]["resets_at"], ZoneInfo("Asia/Kolkata")) == datetime(
        2026, 10, 26, 17, 40, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert parse_claude_usage("nothing here") == []


def test_codex_limits_come_from_every_bucket_with_credits_and_resets_as_notes():
    window = {"usedPercent": 12, "windowDurationMins": 10080, "resetsAt": 1793599181}
    limits = {
        "rateLimits": {"limitId": "codex", "primary": window},
        "rateLimitsByLimitId": {
            "codex": {"limitId": "codex", "limitName": None, "primary": {**window, "windowDurationMins": 300},
                      "secondary": window, "credits": {"hasCredits": True, "unlimited": False, "balance": "73911.98"}},
            "spark": {"limitId": "spark", "limitName": "GPT-6 Spark", "primary": {**window, "usedPercent": 40}},
        },
        "rateLimitResetCredits": {"availableCount": 2},
    }

    found = parse_codex_limits(limits)

    assert [(w["name"], w["used_percent"]) for w in found["windows"]] == [
        ("5 hours", 12.0), ("Week", 12.0), ("Week · GPT-6 Spark", 40.0)]
    assert found["notes"] == ["73,912 credits", "2 free limit resets to use"]
    assert parse_codex_limits({"rateLimits": {"primary": {"usedPercent": 3, "windowDurationMins": 1440},
                                              "credits": {"hasCredits": False, "unlimited": True}}}) == {
        "windows": [{"name": "Day", "used_percent": 3.0, "resets_at": None}], "notes": ["Unlimited credits"]}


# An app-server that answers `initialize`, then asks something of its own under the same id as crewbots' next
# request, then gives that request the reply in $REPLY.
APP_SERVER = """import json, os, sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        print(json.dumps({"id": message["id"], "result": {}}), flush=True)
    elif "id" in message:
        print(json.dumps({"id": message["id"], "method": "item/tool/requestUserInput", "params": {}}), flush=True)
        print(json.dumps({"id": message["id"], **json.loads(os.environ["REPLY"])}), flush=True)
"""


@pytest.fixture
def codex(tmp_path, monkeypatch):
    """A fake `codex` on PATH; set its reply with the returned function."""
    script = tmp_path / "codex"
    script.write_text(f"#!{sys.executable}\nimport sys\nsys.argv[1:] == ['app-server'] or sys.exit(2)\n{APP_SERVER}")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    return lambda reply: monkeypatch.setenv("REPLY", json.dumps(reply))


async def test_codexs_limits_are_read_from_its_app_server(codex):
    codex({"result": {"rateLimits": {"primary": {"usedPercent": 40, "windowDurationMins": 300}}}})

    found = await plans._codex()

    assert found["windows"] == [{"name": "5 hours", "used_percent": 40.0, "resets_at": None}]


async def test_an_error_from_codexs_app_server_is_the_plans_error(codex, monkeypatch):
    monkeypatch.setattr(plans, "_cache", {})
    monkeypatch.setattr(plans, "_asking", {})
    codex({"error": {"code": -32600, "message": "not signed in"}})

    found = (await plans.plans(fresh=True))["codex"]

    assert not found["available"] and "not signed in" in found["error"]


async def test_codexs_models_are_read_from_its_app_server(codex):
    codex({"result": {"data": [{"id": "gpt-6", "displayName": "GPT-6", "supportedReasoningEfforts": ["low"]},
                               {"id": "old", "hidden": True}]}})

    assert [m["id"] for m in await models._codex()] == ["gpt-6"]


@pytest.fixture
def asks(monkeypatch):
    """Fake CLIs that count their asks: each answer's weekly window reads as the ask's number."""
    count = {"claude": 0, "codex": 0}

    def cli(name):
        async def ask():
            count[name] += 1
            await asyncio.sleep(0.01)
            return {"available": True, "error": None, "notes": [],
                    "windows": [{"name": "Week", "used_percent": count[name], "resets_at": None}]}
        return ask

    monkeypatch.setattr(plans, "_claude", cli("claude"))
    monkeypatch.setattr(plans, "_codex", cli("codex"))
    monkeypatch.setattr(plans, "_cache", {})
    monkeypatch.setattr(plans, "_asking", {})
    return count


async def test_plans_are_asked_once_at_a_time_and_kept(asks):
    first, second = await asyncio.gather(plans.plans(), plans.plans())  # the page and the daemon's warm-up at once

    assert asks == {"claude": 1, "codex": 1} and first == second
    assert (await plans.plans())["claude"]["windows"][0]["used_percent"] == 1  # kept: no new ask
    assert (await plans.plans(fresh=True))["claude"]["windows"][0]["used_percent"] == 2


async def test_a_stale_answer_comes_back_at_once_while_a_fresh_one_is_asked_for(asks):
    await plans.plans()
    plans._cache["claude"]["checked_at"] = time.time() - plans.TTL_S - 1

    stale = await plans.plans()
    await asyncio.sleep(0)

    assert stale["claude"]["windows"][0]["used_percent"] == 1 and asks["claude"] == 2  # asked for, not waited on
    assert (await plans.plans(fresh=True))["claude"]["windows"][0]["used_percent"] == 2  # joins that ask
    assert asks["claude"] == 2


async def test_a_failed_ask_keeps_the_last_good_answer(asks, monkeypatch):
    await plans.plans()

    async def broken():
        raise RuntimeError("claude crashed")

    monkeypatch.setattr(plans, "_claude", broken)

    assert (await plans.plans(fresh=True))["claude"]["available"]
    plans._cache.clear()
    assert (await plans.plans(fresh=True))["claude"]["error"] == "claude crashed"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "crew.db")
    yield s
    s.close()


def at(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(timespec="milliseconds")


def test_the_crews_use_by_day_plan_model_and_bot(store):
    bots = {"engineer": bot("engineer", model="gpt-6.1-sol"), "chief": bot("chief", model="opus")}
    bots["engineer"] = bots["engineer"].__class__(**{**bots["engineer"].__dict__, "backend": "codex"})
    chief = store.create_task("chief", "Chat", kind="chat")
    work = store.create_task("engineer", "CSV export")
    review = store.create_task("reviewer:claude:sonnet:high", "Review", kind="review", pr="vir/app#7")
    for task, data in [
        (chief, {"input_tokens": 1000, "cached_input_tokens": 600, "output_tokens": 100, "plan": "claude", "model": "opus",
                 "effort": "high", "cache_write_tokens": 350}),
        (work, {"input_tokens": 5000, "cached_input_tokens": 0, "output_tokens": 500}),  # from before models were kept
        (review, {"input_tokens": 300, "cached_input_tokens": 0, "output_tokens": 50, "plan": "claude",
                  "model": "sonnet", "effort": "high"}),
    ]:
        store.append_event(task.id, kind="usage", data=data)
    store.append_event(work.id, kind="message", data={"from": "chief", "text": "go"})
    store.append_event(work.id, kind="done", data={"final_text": "done"})

    s = summary(store, bots.get, days=30)

    assert s["totals"] == {"claude": 500 + 350, "codex": 5500, "antigravity": 0} and s["turns"] == 3
    assert s["daily"][-1] == {"date": datetime.now().astimezone().date().isoformat(), "claude": 850,
                              "codex": 5500, "antigravity": 0}
    assert s["models"][0] == {"plan": "codex", "model": "gpt-6.1-sol", "tokens": 5500, "input": 5000,
                              "cached": 0, "written": 0}  # put down to its bot's model
    assert s["cache"] == {"input": 6300, "cached": 600, "written": 350}
    assert next(b for b in s["bots"] if b["bot"] == "chief") == {"bot": "chief", "tokens": 500, "input": 1000,
                                                                    "cached": 600, "written": 350}
    assert {b["bot"] for b in s["bots"]} == {"engineer", "chief", "PR reviewer"}
    assert s["efforts"][0] == {"effort": "high", "turns": 2} and s["streak"] == {"longest": 1, "current": 1}
    assert s["longest_turn"]["task_id"] == work.id and s["counts"]["threads"] == 1 and s["counts"]["reviews"] == 1


def test_streaks_count_runs_of_active_days():
    today = datetime(2026, 10, 26).date()
    days = [today - timedelta(days=n) for n in (9, 8, 7, 3, 2, 1)]

    assert _streaks(sorted(days), today) == {"longest": 3, "current": 3}  # ending yesterday still counts
    assert _streaks([today - timedelta(days=5)], today) == {"longest": 1, "current": 0}
    assert _streaks([], today) == {"longest": 0, "current": 0}


def test_a_zone_known_only_by_its_new_name_still_reads(monkeypatch):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    from crewbots import plans

    def strict(name):  # a time zone database without the old names, like Ubuntu's
        if name == "Asia/Calcutta":
            raise ZoneInfoNotFoundError(name)
        return ZoneInfo(name)

    monkeypatch.setattr(plans, "ZoneInfo", strict)
    assert plans._zone("Asia/Calcutta") == ZoneInfo("Asia/Kolkata")
    assert plans._zone("Mars/Olympus") is None  # unknown: this Mac's own time
