"""How well `recall` finds what the crew said and decided: hit@1, hit@5 and mean reciprocal rank over a set of
queries, each with the task it should find.

The built-in set is a crew's worth of distinctive tasks among a few hundred noisy ones that share their words, with
queries of every kind bots ask: words, paraphrases, exact and partial ids, file and code names, error text, and
decisions that changed since. `--from-log` writes the queries bots really asked (the recall log) to a file to label,
for an evaluation from real use.

    uv run scripts/recall_eval.py                  # the built-in set
    uv run scripts/recall_eval.py --cases FILE     # a labelled set: [{"query": ..., "task": <title>}]
    uv run scripts/recall_eval.py --from-log DB    # the recall log in DB, as cases to label
"""

import argparse
import json
import random
import statistics
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from crewbots.crew import Bot, Crew
from crewbots.runner import Runner
from crewbots.store import Store

# (title, the user's request, the bot's reply, its handoff, how many days ago)
TASKS = [
    ("Add CSV export to the reports page", "Add /reports/export.csv and an Export button that uses the current filters",
     "The export streams rows in pages of 500.", "Added ExportButton.tsx and server/routes/export.py, tested in test_reports.py", 2),
    ("Fix the flaky login test", "test_login fails one run in ten", "It races the redirect to /dashboard.",
     "Made test_login wait for the redirect. PR acme/web#409", 3),
    ("Upgrade to React 19", "Can we move to React 19?", "legacy-charts breaks on React 19; recharts replaces it.",
     "Upgraded react and react-dom, swapped legacy-charts for recharts", 4),
    ("Translate the onboarding docs into Spanish", "We need the onboarding guide in Spanish",
     "Translated six pages into docs/onboarding/es/.", "Spanish onboarding docs are in docs/onboarding/es", 5),
    ("Paginate the audit log endpoint", "GET /audit returns everything at once and times out",
     "It now takes a cursor and returns 500 entries a page.", "Cursor pagination on /audit, page size 500. PR acme/api#88", 6),
    ("Release on Fridays", "Let's release every Friday at 4pm", "Noted: releases go out Fridays at 4pm.",
     "Release day set to Friday", 60),
    ("Move release day", "From now on we release on Tuesdays, not Fridays", "Got it: releases move to Tuesdays.",
     "Release day is now Tuesday", 1),
    ("Index events by task", "Opening a long chat is slow", "The events query scans the table.",
     "migrations/0042_add_index.py adds an index on events(task_id, at)", 9),
    ("Investigate the ingest crash", "The ingest worker keeps dying overnight",
     "MemoryError in ingest_worker.py line 212 when a batch passes 10k rows.", "Batches are capped at 5k rows now", 7),
    ("Add truncate to the text helpers", "Add truncate(s, n) to text.py", "It cuts on a word boundary and adds an ellipsis.",
     "truncate(s, n) in text.py, with tests for empty strings and n below 1", 12),
    ("Set up the staging database", "Staging needs its own Postgres", "Created staging-db on port 5433.",
     "Staging Postgres is staging-db:5433, seeded nightly", 15),
    ("Cache GitHub responses", "The inbox hits GitHub's rate limit", "Responses are cached for 90 seconds now.",
     "GitHub responses cached for 90s in github_cache.py", 8),
    ("Dark mode for the settings page", "Settings is unreadable in dark mode", "The toggles used a fixed white.",
     "Settings page follows the theme tokens", 20),
    ("Write the Q3 retro", "Draft the Q3 retro from the reviews", "Three themes: flaky tests, slow reviews, unclear specs.",
     "Q3 retro drafted in docs/retros/2026-q3.md", 25),
    ("Rename the billing service", "billing-svc should be called invoices", "Renamed the service and its queues.",
     "billing-svc is invoices now; queues invoices.created and invoices.paid", 30),
    ("Debug the webhook signature failures", "Stripe webhooks fail signature checks",
     "The raw body was parsed before verifying.", "Verify on the raw body in webhooks/stripe.py", 10),
    ("Speed up the test suite", "The suite takes four minutes", "Fixtures rebuilt the database per test.",
     "Suite runs in 70 seconds with a session-scoped database fixture", 11),
    ("Choose a charting library", "Which charts library should we use?", "recharts: maintained, small, React 19 ready.",
     "Decision: recharts", 4),
    ("Add rate limiting to the public API", "Bots are hammering /api/search", "A token bucket per key, 60 a minute.",
     "Rate limit 60/min per API key in middleware/ratelimit.py", 14),
    ("Fix the timezone bug in reminders", "Reminders fire an hour late since the clocks changed",
     "They used a fixed UTC offset.", "Reminders use zoneinfo now; DST handled", 16),
]
NOISE_WORDS = ("export report login test release docs review fix update page button filter chart data api settings "
               "database cache worker queue service bug plan meeting notes draft check change").split()
CASES = [
    ("csv export filters", "Add CSV export to the reports page"),
    ("export button", "Add CSV export to the reports page"),
    ("ExportButton", "Add CSV export to the reports page"),
    ("ExportButt", "Add CSV export to the reports page"),
    ("make exports respect the filters", "Add CSV export to the reports page"),
    ("flaky login", "Fix the flaky login test"),
    ("acme/web#409", "Fix the flaky login test"),
    ("redirect race", "Fix the flaky login test"),
    ("react 19 upgrade", "Upgrade to React 19"),
    ("legacy-charts", "Upgrade to React 19"),
    ("spanish onboarding", "Translate the onboarding docs into Spanish"),
    ("docs/onboarding/es", "Translate the onboarding docs into Spanish"),
    ("audit pagination", "Paginate the audit log endpoint"),
    ("acme/api#88", "Paginate the audit log endpoint"),
    ("which day do we release", "Move release day"),
    ("release day", "Move release day"),
    ("0042_add_index", "Index events by task"),
    ("slow chat events index", "Index events by task"),
    ("MemoryError", "Investigate the ingest crash"),
    ("ingest_worker.py", "Investigate the ingest crash"),
    ("MemoryErr", "Investigate the ingest crash"),
    ("truncate", "Add truncate to the text helpers"),
    ("truncate(s, n)", "Add truncate to the text helpers"),
    ("staging-db:5433", "Set up the staging database"),
    ("staging postgres port", "Set up the staging database"),
    ("github rate limit cache", "Cache GitHub responses"),
    ("github_cache", "Cache GitHub responses"),
    ("dark mode settings", "Dark mode for the settings page"),
    ("q3 retro", "Write the Q3 retro"),
    ("2026-q3.md", "Write the Q3 retro"),
    ("billing-svc", "Rename the billing service"),
    ("invoices.created", "Rename the billing service"),
    ("stripe webhook signature", "Debug the webhook signature failures"),
    ("webhooks/stripe.py", "Debug the webhook signature failures"),
    ("slow test suite fixtures", "Speed up the test suite"),
    ("which charts library", "Choose a charting library"),
    ("ratelimit.py", "Add rate limiting to the public API"),
    ("rate limit api key", "Add rate limiting to the public API"),
    ("reminders timezone dst", "Fix the timezone bug in reminders"),
    ("zoneinfo", "Fix the timezone bug in reminders"),
]


def build(folder: Path) -> tuple[Runner, dict[str, str]]:
    """The built-in crew: the distinctive tasks, with their dates, among 300 noisy ones."""
    store = Store(folder / "crewbots.db")
    crew = Crew(home=folder, bots={b: Bot(name=b, title=b.title(), description=b, instructions=f"You are the {b}.",
                                         backend="fake") for b in ("chief", "engineer", "critic")}, team_files={})
    runner = Runner(store, crew, {"fake": object()})
    rng = random.Random(7)
    by_title = {}
    asker = store.create_task("chief", "Asking", kind="chat")
    for title, request, reply, done, days in TASKS:
        task = store.create_task("engineer", title)
        by_title[title] = task.id
        _add(store, task.id, days, [("message", {"from": "user", "text": request}), ("text", {"text": reply}),
                                    ("handoff", {"done": done, "output": [], "open": "", "needs": ""})])
    for n in range(300):
        words = rng.sample(NOISE_WORDS, 6)
        task = store.create_task(rng.choice(["chief", "engineer", "critic"]), " ".join(words[:3]).capitalize())
        _add(store, task.id, rng.randint(0, 90), [("message", {"from": "user", "text": " ".join(rng.sample(NOISE_WORDS, 8))}),
                                                  ("text", {"text": " ".join(words) + f" {n}"})])
    return runner, {**by_title, "_asker": asker.id}


def _add(store: Store, task_id: str, days: int, events: list[tuple[str, dict]]) -> None:
    at = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="milliseconds")
    for kind, data in events:
        store.append_event(task_id, kind=kind, data=data)
    for table in ("events", "search"):  # as if it had happened then
        store.db.execute(f"update {table} set at = ? where task_id = ?", (at, task_id))
    store.db.execute("update tasks set created_at = ?, updated_at = ? where id = ?", (at, at, task_id))


def evaluate(runner: Runner, ids: dict[str, str], cases: list[tuple[str, str]]) -> dict[str, float]:
    ranks = []
    for query, title in cases:
        found = [h["task_id"] for h in runner.recall(ids["_asker"], query) if h["task_id"]]
        ranks.append(found.index(ids[title]) + 1 if ids[title] in found else None)
    misses = [q for (q, _), r in zip(cases, ranks, strict=True) if r != 1]
    return {"cases": len(cases), "hit@1": sum(r == 1 for r in ranks) / len(cases),
            "hit@5": sum(bool(r and r <= 5) for r in ranks) / len(cases),
            "mrr": statistics.fmean(1 / r if r else 0 for r in ranks), "not first": misses}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", type=Path, help="a labelled set of queries for the built-in crew")
    parser.add_argument("--from-log", type=Path, help="write the queries in this database's recall log as cases to label")
    args = parser.parse_args()
    if args.from_log:
        store = Store(args.from_log)
        cases = [{"query": e["query"], "task": "", "found": e["hits"][:5]} for e in store.recall_log(1000)]
        print(json.dumps(cases, indent=2))
        return 0
    cases = [(c["query"], c["task"]) for c in json.loads(args.cases.read_text())] if args.cases else CASES
    with tempfile.TemporaryDirectory() as folder:
        runner, ids = build(Path(folder))
        print(json.dumps(evaluate(runner, ids, cases), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
