"""The usage page's numbers: the crew's own use, and where the plans' limits stand."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fakes import bot

from crewbots.plans import parse_claude_usage
from crewbots.store import Store
from crewbots.usage import _streaks, summary

USAGE = """You are currently using your subscription to power your Claude Code usage

Current session: 5% used · resets Oct 26 at 5:40pm (Asia/Calcutta)
Current week (Fable): 0% used · resets Oct 28 at 11:30pm (Asia/Calcutta)
Current week (all models): 12% used · resets Oct 28 at 11:30pm (Asia/Calcutta)

What's contributing to your limits usage?
"""


def test_claudes_usage_command_becomes_windows_with_their_resets():
    now = datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("Asia/Calcutta"))

    windows = parse_claude_usage(USAGE, now)

    assert [(w["name"], w["used_percent"]) for w in windows] == [
        ("5-hour session", 5.0), ("Weekly · Fable", 0.0), ("Weekly", 12.0)]
    assert datetime.fromtimestamp(windows[0]["resets_at"], ZoneInfo("Asia/Calcutta")) == datetime(
        2026, 10, 26, 17, 40, tzinfo=ZoneInfo("Asia/Calcutta"))
    assert parse_claude_usage("nothing here") == []


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
                 "effort": "high"}),
        (work, {"input_tokens": 5000, "cached_input_tokens": 0, "output_tokens": 500}),  # from before models were kept
        (review, {"input_tokens": 300, "cached_input_tokens": 0, "output_tokens": 50, "plan": "claude",
                  "model": "sonnet", "effort": "high"}),
    ]:
        store.append_event(task.id, kind="usage", data=data)
    store.append_event(work.id, kind="message", data={"from": "chief", "text": "go"})
    store.append_event(work.id, kind="done", data={"final_text": "done"})

    s = summary(store, bots.get, days=30)

    assert s["totals"] == {"claude": 500 + 350, "codex": 5500} and s["turns"] == 3
    assert s["daily"][-1] == {"date": datetime.now().astimezone().date().isoformat(), "claude": 850, "codex": 5500}
    assert s["models"][0] == {"plan": "codex", "model": "gpt-6.1-sol", "tokens": 5500}  # put down to its bot's model
    assert {b["bot"] for b in s["bots"]} == {"engineer", "chief", "PR reviewer"}
    assert s["efforts"][0] == {"effort": "high", "turns": 2} and s["streak"] == {"longest": 1, "current": 1}
    assert s["longest_turn"]["task_id"] == work.id and s["counts"]["threads"] == 1 and s["counts"]["reviews"] == 1


def test_streaks_count_runs_of_active_days():
    today = datetime(2026, 10, 26).date()
    days = [today - timedelta(days=n) for n in (9, 8, 7, 3, 2, 1)]

    assert _streaks(sorted(days), today) == {"longest": 3, "current": 3}  # ending yesterday still counts
    assert _streaks([today - timedelta(days=5)], today) == {"longest": 1, "current": 0}
    assert _streaks([], today) == {"longest": 0, "current": 0}
