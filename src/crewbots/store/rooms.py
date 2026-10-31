"""Group chats, their messages, how far each conversation has been seen, and the chats pinned to the top."""

import json
import uuid
from typing import Any

from crewbots.store.records import _now, _room, _room_message

ROOM_SETTINGS = {"title", "lead", "members", "max_depth", "stopped", "muted", "archived_at"}
JSON_COLUMNS = {"members", "stopped"}


class Rooms:
    """The part of Store for group chats, and how far each conversation has been seen."""

    def create_room(self, title: str, lead: str, members: list[str], crew: bool = False) -> dict[str, Any]:
        room_id = f"r_{uuid.uuid4().hex[:10]}"
        now = _now()
        self.db.execute("insert into rooms (id, title, lead, members, created_at, updated_at, crew) "
                        "values (?, ?, ?, ?, ?, ?, ?)", (room_id, title, lead, json.dumps(members), now, now, int(crew)))
        return self.room(room_id)

    def room(self, room_id: str) -> dict[str, Any]:
        row = self.db.execute("select * from rooms where id = ?", (room_id,)).fetchone()
        if row is None:
            raise KeyError(room_id)
        return _room(row)

    def update_room(self, room_id: str, **changes: Any) -> dict[str, Any]:
        """Changes a room's settings (see ROOM_SETTINGS), not when it was last talked in."""
        if unknown := changes.keys() - ROOM_SETTINGS:
            raise ValueError(f"not a room setting: {sorted(unknown)}")
        values = [json.dumps(v) if k in JSON_COLUMNS and v is not None else v for k, v in changes.items()]
        self.db.execute(f"update rooms set {', '.join(f'{k} = ?' for k in changes)} where id = ?", (*values, room_id))
        return self.room(room_id)

    def delete_room(self, room_id: str) -> None:
        """Deletes a room and what was said in it. Its seats stay, as the record of what their turns cost."""
        seats = [r["id"] for r in self.db.execute("select id from tasks where kind = 'seat' and room_id = ?", (room_id,))]
        self.db.execute("delete from reads where key = ? or key in (select 'seat:' || value from json_each(?))",
                        (f"room:{room_id}", json.dumps(seats)))
        self.db.execute("delete from room_messages where room_id = ?", (room_id,))
        self.db.execute("delete from pins where key = ?", (f"room:{room_id}",))
        self.db.execute("delete from rooms where id = ?", (room_id,))

    def rooms(self) -> list[dict[str, Any]]:
        rows = self.db.execute("select * from rooms order by created_at").fetchall()
        return [_room(r) for r in rows]

    def add_room_message(self, room_id: str, author: str, text: str, depth: int = 0,
                         attachments: list[dict[str, Any]] | None = None, folder: str | None = None) -> dict[str, Any]:
        now = _now()
        cur = self.db.execute(
            "insert into room_messages (room_id, author, text, depth, at, attachments, folder) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (room_id, author, text, depth, now, json.dumps(attachments) if attachments else None, folder))
        self.db.execute("update rooms set updated_at = ? where id = ?", (now, room_id))
        return {"id": cur.lastrowid, "room_id": room_id, "author": author, "text": text, "depth": depth, "at": now,
                "attachments": attachments or [], "folder": folder}

    def room_messages(self, room_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.db.execute("select * from room_messages where room_id = ? and id > ? order by id limit ?",
                               (room_id, after, limit)).fetchall()
        return [_room_message(r) for r in rows]

    def recent_room_messages(self, room_id: str, through: int, limit: int) -> list[dict[str, Any]]:
        """The room's last `limit` messages up to the one with id `through`, oldest first."""
        rows = self.db.execute("select * from room_messages where room_id = ? and id <= ? order by id desc limit ?",
                               (room_id, through, limit)).fetchall()
        return [_room_message(r) for r in reversed(rows)]

    def room_messages_by(self, room_id: str, author: str, after: str, limit: int) -> list[dict[str, Any]]:
        """`author`'s latest `limit` messages in the room after time `after`, oldest first."""
        rows = self.db.execute("select * from room_messages where room_id = ? and author = ? and at > ? "
                               "order by id desc limit ?", (room_id, author, after, limit)).fetchall()
        return [_room_message(r) for r in reversed(rows)]

    def last_room_message(self, room_id: str) -> dict[str, Any] | None:
        row = self.db.execute("select * from room_messages where room_id = ? order by id desc limit 1",
                              (room_id,)).fetchone()
        return _room_message(row) if row else None

    def seen(self, key: str) -> int:
        row = self.db.execute("select seen from reads where key = ?", (key,)).fetchone()
        return row["seen"] if row else 0

    def rewind_seat_seen(self, task_id: str, seen: int) -> None:
        """Keep messages queued behind a stopped seat turn unread until a later wake."""
        self.db.execute("update reads set seen = min(seen, ?) where key = ?", (seen, f"seat:{task_id}"))

    def chat_muted(self, bot: str) -> bool:
        return bool(self.db.execute("select 1 from chat_mutes where bot = ?", (bot,)).fetchone())

    def set_chat_muted(self, bot: str, muted: bool) -> None:
        if muted:
            self.db.execute("insert or ignore into chat_mutes (bot) values (?)", (bot,))
        else:
            self.db.execute("delete from chat_mutes where bot = ?", (bot,))

    def pins(self) -> list[str]:
        """The chats pinned to the top of the sidebar, in the user's order: `bot:<name>` and `room:<id>`."""
        return [r["key"] for r in self.db.execute("select key from pins order by position")]

    def set_pins(self, keys: list[str]) -> list[str]:
        self.db.execute("delete from pins")
        self.db.executemany("insert into pins (key, position) values (?, ?)", [(k, i) for i, k in enumerate(keys)])
        return self.pins()

    def mark_seen(self, key: str, seen: int) -> None:
        self.db.execute("insert into reads (key, seen) values (?, ?) on conflict(key) do update set"
                        " seen = max(seen, excluded.seen)", (key, seen))
