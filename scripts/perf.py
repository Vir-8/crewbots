"""How fast the daemon answers on a crew that has been busy for months, against budgets.

Builds a big crew in a temp folder (or reuses one with --keep), serves it over real HTTP on a spare port, and times
the routes the app opens with, a long chat, a group chat's messages and the live stream. Exits 1 when a median is over
its budget, so it can gate a release.

    uv run scripts/perf.py            # build, measure, check
    uv run scripts/perf.py --keep DIR # keep the generated crew in DIR, and reuse it next time
"""

import argparse
import asyncio
import json
import random
import secrets
import shutil
import socket
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn
import websockets

from crewbots import crew as crew_module
from crewbots.api import create_app
from crewbots.events import Done, SessionStarted, Text
from crewbots.runner import Runner
from crewbots.store import Status, Store

BOTS = 10
CHAT_EVENTS = 5_000  # per bot: months of a chat
THREADS = 2_000
THREAD_EVENTS = 100
ROOMS = 4
ROOM_MESSAGES = 5_000
LONG_CHAT = 2_000  # the events of the chat the app opens

# Median milliseconds each may take, measured on this Mac (M-series) with the crew above. Generous enough not to flake,
# tight enough to catch a query that stops using its index or a route that reads a whole table.
BUDGETS_MS = {
    "GET /tasks?limit=1000": 120,
    "GET /chats": 60,
    "GET /rooms/{id}/messages": 60,
    "GET /tasks/{id}/events (2,000)": 80,
    "GET /events/recent?limit=400": 40,
    "GET /events/cursor": 10,
    "GET /search (two words)": 60,
    "GET /search (a word in everything)": 250,
    "GET /bots/{bot}/profile": 60,
    "live: a message reaches a client": 30,
}
RUNS = 15


class Instant:
    """A backend that answers at once, so the live stream is timed, not a model."""

    name = "fake"

    async def run(self, spec):
        yield SessionStarted(session_id="s")
        yield Text(text="ok")
        yield Done(final_text="ok")


def build(home: Path) -> None:
    started = time.perf_counter()
    crew_module.init(home)
    for n in range(BOTS - len(list((home / "bots").glob("*.md")))):
        (home / "bots" / f"helper{n}.md").write_text(f"---\ndescription: helper {n}\n---\nYou help.\n")
    crew = crew_module.load(home)
    store = Store(home / "crewbots.db")
    runner = Runner(store, crew, {"fake": Instant()})
    # Text like a crew's: a few common words in everything, and a large vocabulary, so a search matches what a real
    # one would rather than every row.
    rng = random.Random(7)
    common = "the a to and of in for is it on".split()
    vocabulary = [f"{rng.choice('bcdfghjklmnprstvw')}{rng.choice('aeiou')}{rng.choice('bcdfgklmnprst')}"
                  f"{rng.choice('aeiou')}{rng.choice('nrstl')}{n}" for n in range(5000)]
    vocabulary[:4] = ["pagination", "export", "flaky", "redirect"]

    def sentence(i: int) -> str:
        return " ".join(rng.choice(common) if k % 3 == 0 else rng.choice(vocabulary) for k in range(14))

    store.db.execute("begin")
    for b, bot in enumerate(crew.bots):
        chat = runner.chat(bot)
        size = LONG_CHAT if b == 0 else CHAT_EVENTS
        for i in range(size):
            kind = ("message", "text", "tool_call", "tool_result", "usage")[i % 5]
            data = {"message": {"from": "user", "text": sentence(i)}, "text": {"text": sentence(i)},
                    "tool_call": {"id": f"c{i}", "name": "Read", "input": {"file_path": f"src/{i}.py"}},
                    "tool_result": {"id": f"c{i}", "output": sentence(i) * 4, "is_error": False},
                    "usage": {"input_tokens": 1000, "output_tokens": 50, "cached_input_tokens": 900}}[kind]
            store.append_event(chat.id, kind=kind, data=data)
    bots = list(crew.bots)
    for t in range(THREADS):
        task = store.create_task(bots[t % len(bots)], f"Thread {t}: {sentence(t)}", status=Status.IDLE)
        for i in range(THREAD_EVENTS):
            store.append_event(task.id, kind=("text", "tool_call", "tool_result")[i % 3],
                               data={"text": sentence(t + i), "id": f"c{i}", "name": "Bash",
                                     "input": {"command": "pytest -q"}, "output": sentence(i), "is_error": False})
    for r in range(ROOMS):
        room = store.create_room(f"Room {r}", bots[0], bots[:4])
        for i in range(ROOM_MESSAGES):
            store.add_room_message(room["id"], bots[i % 4] if i % 3 else "user", sentence(i))
    store.db.execute("commit")
    events = store.db.execute("select count(*) from events").fetchone()[0]
    store.close()
    print(f"built a crew of {BOTS} bots, {THREADS:,} threads, {events:,} events and {ROOMS * ROOM_MESSAGES:,} group "
          f"chat messages in {time.perf_counter() - started:.1f}s")


def serve(home: Path) -> tuple[str, str, Runner]:
    crew = crew_module.load(home)
    store = Store(home / "crewbots.db")
    runner = Runner(store, crew, {"fake": Instant()})
    for bot in crew.bots.values():  # every bot answers at once
        crew.bots[bot.name] = bot.__class__(**{**bot.__dict__, "backend": "fake"})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    token = secrets.token_urlsafe(16)
    server = uvicorn.Server(uvicorn.Config(create_app(runner, token, port), host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}/v1", token, runner


def timed(client: httpx.Client, path: str) -> float:
    started = time.perf_counter()
    client.get(path).raise_for_status()
    return (time.perf_counter() - started) * 1000


async def live_latency(base: str, token: str, client: httpx.Client, thread_id: str) -> list[float]:
    """From sending a message to a client of /v1/live hearing of it."""
    cursor = client.get("/events/cursor").json()["id"]
    url = base.replace("http", "ws") + f"/live?after={cursor}"
    took = []
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {token}"},
                                  max_size=None) as ws:
        for n in range(RUNS):
            text = f"ping {n} {secrets.token_hex(4)}"
            started = time.perf_counter()
            await asyncio.to_thread(client.post, f"/tasks/{thread_id}/messages", json={"text": text})
            while True:
                message = json.loads(await ws.recv())
                event = message.get("event") or {}
                if message.get("type") == "event" and event.get("kind") == "message" and event["data"].get("text") == text:
                    took.append((time.perf_counter() - started) * 1000)
                    break
            await asyncio.sleep(0.05)
    return took


def measure(home: Path) -> dict[str, list[float]]:
    base, token, runner = serve(home)
    results: dict[str, list[float]] = {}
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30) as client:
        bots = [b["name"] for b in client.get("/bots").json()]
        chats = client.get("/chats").json()
        long_chat = next(c["task_id"] for c in chats if c["bot"] == bots[0])
        room = next(c["room"]["id"] for c in chats if c["kind"] == "group" and c["title"].startswith("Room"))
        thread = client.get("/tasks", params={"limit": 1}).json()[0]["id"]
        paths = {
            "GET /tasks?limit=1000": "/tasks?limit=1000",
            "GET /chats": "/chats",
            "GET /rooms/{id}/messages": f"/rooms/{room}/messages",
            "GET /tasks/{id}/events (2,000)": f"/tasks/{long_chat}/events?limit=5000",
            "GET /events/recent?limit=400": "/events/recent?limit=400",
            "GET /events/cursor": "/events/cursor",
            "GET /search (two words)": "/search?q=pagination%20export",
            "GET /search (a word in everything)": "/search?q=the",
            "GET /bots/{bot}/profile": f"/bots/{bots[0]}/profile",
        }
        for name, path in paths.items():
            timed(client, path)  # warm
            results[name] = [timed(client, path) for _ in range(RUNS)]
        results["live: a message reaches a client"] = asyncio.run(live_latency(base, token, client, thread))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", type=Path, help="build the crew here, or reuse the one already here")
    args = parser.parse_args()
    home = args.keep or Path(tempfile.mkdtemp(prefix="crewbots-perf-")) / "crew"
    try:
        if not (home / "crewbots.db").exists():
            build(home)
        results = measure(home)
    finally:
        if not args.keep:
            shutil.rmtree(home.parent, ignore_errors=True)
    over = 0
    print(f"\n{'':44} {'median':>8} {'p95':>8} {'budget':>8}")
    for name, times in results.items():
        median = statistics.median(times)
        p95 = sorted(times)[int(len(times) * 0.95) - 1]
        budget = BUDGETS_MS[name]
        over += median > budget
        print(f"{name:44} {median:7.1f}ms {p95:7.1f}ms {budget:6d}ms{'  OVER' if median > budget else ''}")
    print(f"\n{'all within budget' if not over else f'{over} over budget'}")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
