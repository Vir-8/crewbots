"""Chats and group chats: the conversation list, reading them, and their messages."""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from crewbots.api.context import Context
from crewbots.api.schemas import ChatOut, Message, NewRoom, RoomChange, RoomMessageOut, RoomOut, TaskOut
from crewbots.store import Status


def routes(c: Context) -> APIRouter:
    runner, store = c.runner, c.store
    attached = c.attached
    v1 = APIRouter()

    def busy(task_id: str) -> bool:
        return any(t.active or t.status is Status.HELD or t.id in runner.running() for t in store.tree(task_id))

    def direct(bot: str) -> dict[str, Any]:
        chat = runner.chat(bot)
        said = store.last_event(chat.id, ("text", "shared"))
        asked = store.last_event(chat.id, ("message",))
        last = None
        if asked and asked.data.get("from") not in ("crewbots", "room") and (not said or asked.id > said.id):
            last = {"author": asked.data.get("from", "user"), "text": asked.data.get("text", ""), "at": asked.at}
        elif said:
            last = {"author": bot, "text": _said(said.data), "at": said.at}
        unread = store.count_events_after(chat.id, store.seen(f"chat:{chat.id}"), ("text", "shared", "escalation"))
        return {"key": f"bot:{bot}", "kind": "direct", "title": runner.crew.bots[bot].title, "bot": bot,
                "task_id": chat.id, "room": None, "last": last, "unread": unread, "working": busy(chat.id),
                "updated_at": last["at"] if last else chat.created_at}

    def group(room: dict[str, Any]) -> dict[str, Any]:
        message = store.last_room_message(room["id"])
        seen = store.seen(f"room:{room['id']}")
        unread = sum(1 for m in store.room_messages(room["id"], after=seen) if m["author"] != "user")
        seats = [store.seat_of(room["id"], b) for b in room["members"]]
        last = {"author": message["author"], "text": _said(message), "at": message["at"]} if message else None
        return {"key": f"room:{room['id']}", "kind": "group", "title": room["title"], "bot": None, "task_id": None,
                "room": room, "last": last,
                "unread": unread, "working": any(s and busy(s.id) for s in seats), "updated_at": room["updated_at"]}

    def room_or_404(room_id: str) -> dict[str, Any]:
        try:
            return store.room(room_id)
        except KeyError:
            raise HTTPException(404, f"no group chat {room_id}") from None

    @v1.get("/chats", response_model=list[ChatOut])
    async def chats():
        """Every conversation: one with each bot, then the group chats (the crew's own is made the first time)."""
        if runner.crew.bots:
            runner.crew_room()
        return [direct(b) for b in runner.crew.bots] + [group(r) for r in store.rooms()]

    @v1.post("/chats/bot/{bot}/messages", response_model=TaskOut)
    async def message_bot(bot: str, body: Message):
        return runner.say(bot, body.text, attachments=attached(body)).to_json()

    @v1.post("/chats/{key}/read")
    async def read(key: str):
        """Marks a conversation seen up to now."""
        kind, _, name = key.partition(":")
        if kind == "bot":
            chat = runner.chat(name)
            store.mark_seen(f"chat:{chat.id}", store.latest_event_id())
        elif kind == "room":
            room_or_404(name)
            last = store.last_room_message(name)
            store.mark_seen(f"room:{name}", last["id"] if last else 0)
        else:
            raise HTTPException(404, f"no conversation {key}")
        return {"ok": True}

    @v1.post("/rooms", status_code=201, response_model=RoomOut)
    async def new_room(body: NewRoom):
        return runner.create_room(body.title, body.members, body.lead)

    @v1.get("/rooms/{room_id}/messages", response_model=list[RoomMessageOut])
    async def room_messages(room_id: str, after: int = 0, limit: int = Query(1000, le=10_000)):
        room_or_404(room_id)
        return store.room_messages(room_id, after=after, limit=limit)

    @v1.post("/rooms/{room_id}/messages", status_code=201, response_model=RoomMessageOut)
    async def post_message(room_id: str, body: Message):
        room_or_404(room_id)
        return runner.post(room_id, "user", body.text, attachments=attached(body))

    @v1.get("/rooms", response_model=list[RoomOut])
    async def rooms():
        return store.rooms()

    @v1.patch("/rooms/{room_id}", response_model=RoomOut)
    async def change_room(room_id: str, body: RoomChange):
        room = room_or_404(room_id)
        if "max_depth" in body.model_fields_set:
            room = runner.set_room_limit(room_id, body.max_depth)
        if body.model_fields_set - {"max_depth"}:
            room = await runner.change_room(room_id, body.title, body.lead, body.members, body.muted, body.archived)
        return room

    @v1.delete("/rooms/{room_id}", status_code=204)
    async def delete_room(room_id: str):
        room_or_404(room_id)
        await runner.delete_room(room_id)

    @v1.post("/rooms/{room_id}/continue", response_model=RoomOut)
    async def carry_on(room_id: str):
        room_or_404(room_id)
        return runner.carry_on(room_id)

    return v1


def _said(message: dict[str, Any]) -> str:
    """What a chat's latest message reads as in the list: its text, or the files it came with."""
    return message.get("text") or ", ".join(a["name"] for a in message.get("attachments") or [])
