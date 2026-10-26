"""The crew's record: recent events, search, and the live stream."""

import asyncio
import contextlib

from fastapi import APIRouter, HTTPException, Query, WebSocket

from crewbots.api.context import Context
from crewbots.api.schemas import CursorOut, EventOut, LiveMessage, SearchHit


def routes(c: Context) -> APIRouter:
    runner, store = c.runner, c.store
    allowed = c.allowed
    v1 = APIRouter()

    @v1.get("/search", response_model=list[SearchHit])
    async def search(q: str, limit: int = Query(20, le=200)):
        """Everything the crew said or decided that matches `q`."""
        return store.search(q, limit=limit)

    @v1.get("/events/recent", response_model=list[EventOut])
    async def recent_events(limit: int = Query(200, le=2000), kinds: str | None = None):
        """The crew's newest events, oldest first; `kinds` is a comma-separated filter (e.g. handoff,review)."""
        wanted = tuple(k for k in kinds.split(",") if k) if kinds else None
        return [e.to_json() for e in store.recent_events(limit, wanted)]

    @v1.get("/events/cursor", response_model=CursorOut)
    async def events_cursor():
        """The newest event's id, so a client can follow /live from now on instead of replaying the history."""
        return {"id": store.latest_event_id()}

    @v1.websocket("/live")
    async def live(ws: WebSocket, after: int = 0, token_param: str | None = Query(None, alias="token")):
        """Every event after `after`, then everything new: task updates and events, as they happen.

        Browsers cannot set headers on a WebSocket, so the token may come as ?token= instead.
        """
        auth = ws.headers.get("authorization", "")
        credential = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else token_param
        if not allowed(ws.headers.get("host"), credential):
            await ws.close(code=4403)
            return
        await ws.accept()
        with runner.bus.subscribe() as queue:  # subscribe first, so nothing falls between the backlog and live
            last = after
            for event in store.events(after=after, limit=100_000):
                await ws.send_json({"type": "event", "event": event.to_json()})
                last = event.id
            receiver = asyncio.create_task(_until_closed(ws))
            try:
                while not receiver.done():
                    getter = asyncio.create_task(queue.get())
                    finished, _ = await asyncio.wait({getter, receiver}, return_when=asyncio.FIRST_COMPLETED)
                    if getter not in finished:
                        getter.cancel()
                        break
                    message = getter.result()
                    if message["type"] == "event" and message["event"]["id"] <= last:
                        continue  # already sent in the backlog
                    await ws.send_json(message)
            finally:
                receiver.cancel()

    @v1.get("/live/schema", response_model=LiveMessage, include_in_schema=True)
    async def live_schema():
        """Not for calling: it describes the messages /v1/live sends, so they are in the schema the app's types
        are generated from."""
        raise HTTPException(404, "the messages are sent on /v1/live")

    return v1


async def _until_closed(ws: WebSocket) -> None:
    with contextlib.suppress(Exception):
        while True:
            await ws.receive_text()
