"""The crew's own usage, summed up for the usage page: by day and plan, its peaks and streaks, and what spent it.

Tokens are fresh ones (input the model hadn't cached, plus output), the same count budgets use. A turn recorded
before usage carried its model is put down to its bot's model now.
"""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from crewbots.store import Store

REVIEWER = "PR reviewer"


def summary(store: Store, bot_for, days: int = 371) -> dict[str, Any]:
    today = datetime.now().astimezone().date()
    first = today - timedelta(days=days - 1)
    since = datetime.combine(first, datetime.min.time()).astimezone().astimezone(UTC).isoformat(timespec="milliseconds")
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"claude": 0, "codex": 0})
    models: Counter[tuple[str, str]] = Counter()
    bots: Counter[str] = Counter()
    efforts: Counter[str] = Counter()
    turns = 0
    for row in store.usage_rows(since):
        fresh = max(0, int(row.get("input_tokens") or 0) - int(row.get("cached_input_tokens") or 0)) + int(
            row.get("output_tokens") or 0)
        bot = bot_for(row["bot"])
        plan = row.get("plan") or (bot.backend.split("-")[0] if bot else "claude")
        model = row.get("model") or (bot.model if bot and bot.model else "default")
        day = datetime.fromisoformat(row["at"]).astimezone().date().isoformat()
        by_day[day][plan if plan in ("claude", "codex") else "claude"] += fresh
        models[(plan, model)] += fresh
        bots[REVIEWER if row["kind"] == "review" else row["bot"]] += fresh
        efforts[row.get("effort") or (bot.effort if bot and bot.effort else "default")] += 1
        turns += 1
    daily = [{"date": d.isoformat(), **by_day.get(d.isoformat(), {"claude": 0, "codex": 0})}
             for d in (first + timedelta(days=n) for n in range(days))]
    totals = {"claude": sum(d["claude"] for d in daily), "codex": sum(d["codex"] for d in daily)}
    peak = max(daily, key=lambda d: d["claude"] + d["codex"])
    longest, longest_task = 0.0, None
    for task_id, asked, finished in store.turn_spans():
        seconds = (datetime.fromisoformat(finished) - datetime.fromisoformat(asked)).total_seconds()
        if longest_task is None or seconds > longest:
            longest, longest_task = seconds, task_id
    active = [date.fromisoformat(d["date"]) for d in daily if d["claude"] + d["codex"] > 0]
    return {
        "days": days,
        "daily": daily,
        "totals": totals,
        "turns": turns,
        "peak": {"date": peak["date"], "tokens": peak["claude"] + peak["codex"]} if active else None,
        "longest_turn": {"seconds": round(longest), "task_id": longest_task,
                         "title": store.get_task(longest_task).title} if longest_task else None,
        "streak": _streaks(active, today),
        "models": [{"plan": p, "model": m, "tokens": t} for (p, m), t in models.most_common(8)],
        "bots": [{"bot": b, "tokens": t} for b, t in bots.most_common(8)],
        "efforts": [{"effort": e, "turns": n} for e, n in efforts.most_common()],
        "counts": store.count_since(since),
    }


def _streaks(active: list[date], today: date) -> dict[str, int]:
    """The longest run of days with any use, and the one going now (today, or ending yesterday)."""
    longest = run = 0
    previous: date | None = None
    for day in active:
        run = run + 1 if previous and day - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    current = run if previous and today - previous <= timedelta(days=1) else 0
    return {"longest": longest, "current": current}
