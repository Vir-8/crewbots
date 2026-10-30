"""The crew's own usage, summed up for the analytics page: by day and plan, its peaks and streaks, and what spent it.

Tokens are fresh ones (input the model hadn't cached, plus output), the same count budgets use. Input the model
had cached is counted apart, for how well the prompt cache works. A turn recorded before usage carried its model is
put down to its bot's model now.
"""

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from crewbots.runner import common
from crewbots.store import Store

REVIEWER = "PR reviewer"
PLANS = ("claude", "codex", "antigravity")


def summary(store: Store, bot_for, days: int = 371) -> dict[str, Any]:
    today = datetime.now().astimezone().date()
    first = today - timedelta(days=days - 1)
    since = datetime.combine(first, datetime.min.time()).astimezone().astimezone(UTC).isoformat(timespec="milliseconds")
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(PLANS, 0))
    models: Counter[tuple[str, str]] = Counter()
    bots: Counter[str] = Counter()
    cache: dict[str, Counter[str]] = defaultdict(Counter)  # "all", "model:<plan>:<model>", "bot:<bot>" → input, cached, written
    efforts: Counter[str] = Counter()
    turns = 0
    for row in store.usage_by(since):
        fresh = int(row["fresh"] or 0)
        bot = bot_for(row["bot"])
        plan = row["plan"] or (common.plan(bot.backend) if bot else "claude")
        model = row["model"] or (bot.model if bot and bot.model else "default")
        by_day[row["day"]][plan if plan in PLANS else "claude"] += fresh
        models[(plan, model)] += fresh
        who = REVIEWER if row["kind"] == "review" else row["bot"]
        bots[who] += fresh
        for key in ("all", f"model:{plan}:{model}", f"bot:{who}"):
            cache[key]["input"] += int(row["input"] or 0)
            cache[key]["cached"] += int(row["cached"] or 0)
            cache[key]["written"] += int(row["written"] or 0)
        efforts[row["effort"] or (bot.effort if bot and bot.effort else "default")] += row["turns"]
        turns += row["turns"]
    daily = [{"date": d.isoformat(), **by_day.get(d.isoformat(), dict.fromkeys(PLANS, 0))}
             for d in (first + timedelta(days=n) for n in range(days))]
    totals = {plan: sum(d[plan] for d in daily) for plan in PLANS}
    peak = max(daily, key=lambda d: sum(d[p] for p in PLANS))
    longest = store.longest_turn(since)
    active = [date.fromisoformat(d["date"]) for d in daily if sum(d[p] for p in PLANS) > 0]
    return {
        "days": days,
        "daily": daily,
        "totals": totals,
        "turns": turns,
        "peak": {"date": peak["date"], "tokens": sum(peak[p] for p in PLANS)} if active else None,
        "longest_turn": {"seconds": round(longest[1]), "task_id": longest[0],
                         "title": store.get_task(longest[0]).title} if longest else None,
        "streak": _streaks(active, today),
        "models": [{"plan": p, "model": m, "tokens": t, **_cached(cache[f"model:{p}:{m}"])}
                   for (p, m), t in models.most_common(8)],
        "bots": [{"bot": b, "tokens": t, **_cached(cache[f"bot:{b}"])} for b, t in bots.most_common(8)],
        "cache": _cached(cache["all"]),
        "efforts": [{"effort": e, "turns": n} for e, n in efforts.most_common()],
        "counts": store.count_since(since),
    }


def _cached(counts: Counter[str]) -> dict[str, int]:
    return {"input": counts["input"], "cached": counts["cached"], "written": counts["written"]}


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
