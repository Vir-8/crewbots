"""Group chats, their messages, and how far each conversation has been seen."""

import json
import uuid
from typing import Any

from crewbots.store.records import _now, _room_message


class Rooms:
    """The part of Store for group chats, and how far each conversation has been seen."""

    def create_room(self, title: str, lead: str, members: list[str]) -> dict[str, Any]:
        room_id = f"r_{uuid.uuid4().hex[:10]}"
        now = _now()
        self.db.execute("insert into rooms (id, title, lead, members, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
                        (room_id, title, lead, json.dumps(members), now, now))
        return self.room(room_id)

    def room(self, room_id: str) -> dict[str, Any]:
        row = self.db.execute("select * from rooms where id = ?", (room_id,)).fetchone()
        if row is None:
            raise KeyError(room_id)
        return {**dict(row), "members": json.loads(row["members"])}

    def set_room_members(self, room_id: str, members: list[str]) -> dict[str, Any]:
        self.db.execute("update rooms set members = ? where id = ?", (json.dumps(members), room_id))
        return self.room(room_id)

    def rooms(self) -> list[dict[str, Any]]:
        rows = self.db.execute("select * from rooms order by created_at").fetchall()
        return [{**dict(r), "members": json.loads(r["members"])} for r in rows]

    def add_room_message(self, room_id: str, author: str, text: str, depth: int = 0,
                         attachments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        now = _now()
        cur = self.db.execute(
            "insert into room_messages (room_id, author, text, depth, at, attachments) values (?, ?, ?, ?, ?, ?)",
            (room_id, author, text, depth, now, json.dumps(attachments) if attachments else None))
        self.db.execute("update rooms set updated_at = ? where id = ?", (now, room_id))
        return {"id": cur.lastrowid, "room_id": room_id, "author": author, "text": text, "depth": depth, "at": now,
                "attachments": attachments or []}

    def room_messages(self, room_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.db.execute("select * from room_messages where room_id = ? and id > ? order by id limit ?",
                               (room_id, after, limit)).fetchall()
        return [_room_message(r) for r in rows]

    def last_room_message(self, room_id: str) -> dict[str, Any] | None:
        row = self.db.execute("select * from room_messages where room_id = ? order by id desc limit 1",
                              (room_id,)).fetchone()
        return _room_message(row) if row else None

    def seen(self, key: str) -> int:
        row = self.db.execute("select seen from reads where key = ?", (key,)).fetchone()
        return row["seen"] if row else 0

    def mark_seen(self, key: str, seen: int) -> None:
        self.db.execute("insert into reads (key, seen) values (?, ?) on conflict(key) do update set"
                        " seen = max(seen, excluded.seen)", (key, seen))
