"""Everything the crew has done, in one SQLite file: tasks, and every event of every run.

A task is the unit of work and of conversation. It has exactly one owner bot, its own backend session (so a follow-up
message resumes where it left off), optionally its own worktree, and a transcript made of its events. There are five
kinds:

- a **chat**: a bot's one ongoing conversation with the user, which never ends;
- a **seat**: a bot's own conversation inside a group chat (a room), fed the room's new messages;
- a **thread**: a piece of work, opened from a chat, a seat, a routine or another thread;
- a **review**: a code review of a pull request;
- an **ask**: a question one bot put to another, answered read-only.

A room is a group chat: the user and some bots, with one of them the lead. Its messages are kept on their own.

Store's parts are by area: tasks, events, rooms, search, code review's drafts, routines, what happened around the
crew, and what bots asked the user to allow; `db.py` has the database itself and its migrations.
"""

import json
from pathlib import Path
from typing import Any

from crewbots import timefmt
from crewbots.store.db import Connection, migrate
from crewbots.store.drafts import Drafts
from crewbots.store.events import Events
from crewbots.store.happenings import Happenings
from crewbots.store.permissions import Permissions
from crewbots.store.rooms import Rooms
from crewbots.store.routines import Routines
from crewbots.store.search import Search
from crewbots.store.tasks import Tasks


class Store(Tasks, Events, Rooms, Search, Drafts, Routines, Happenings, Permissions):
    def __init__(self, path: Path | str):
        self.db = Connection(path)
        self.db.execute("pragma journal_mode=wal")
        self.db.execute("pragma foreign_keys=on")
        migrate(self.db, self._index_history)
        timefmt.set_zone(self.timezone())

    def close(self) -> None:
        self.db.close()

    def _setting(self, key: str) -> Any:
        row = self.db.execute("select value from crew_settings where key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_setting(self, key: str, value: Any, *, only_if_unset: bool = False) -> None:
        """The value column has INTEGER affinity, so a name is stored with a prefix ("zone:", "bot:") that keeps it
        text, even a handle such as "001"."""
        conflict = "do nothing" if only_if_unset else "do update set value = excluded.value"
        self.db.execute(f"insert into crew_settings (key, value) values (?, ?) on conflict(key) {conflict}", (key, value))

    def auto_merge(self) -> bool:
        return self._setting("auto_merge") == 1

    def set_auto_merge(self, enabled: bool) -> None:
        self._set_setting("auto_merge", int(enabled))

    def landed_seen(self) -> int:
        """When the user last looked at what landed, in milliseconds since the epoch; 0 if never."""
        return int(self._setting("landed_seen") or 0)

    def set_landed_seen(self, at_ms: int) -> None:
        self._set_setting("landed_seen", at_ms)

    def rooms_made(self) -> dict[str, str]:
        """The crew's group chat files made into group chats so far: file name → the group chat's id."""
        made = self._setting("rooms_made")
        return json.loads(made) if made else {}

    def set_rooms_made(self, made: dict[str, str]) -> None:
        self._set_setting("rooms_made", json.dumps(made))

    def timezone(self) -> str | None:
        """The crew's chosen time zone; None until one is chosen, when the machine's own is used."""
        zone = self._setting("timezone")
        return str(zone).removeprefix("zone:") if zone is not None else None

    def set_timezone(self, name: str | None) -> None:
        if name is None:
            self.db.execute("delete from crew_settings where key = 'timezone'")
        else:
            self._set_setting("timezone", "zone:" + name)
        timefmt.set_zone(name)

    def chief(self) -> str | None:
        chief = self._setting("chief")
        return str(chief).removeprefix("bot:") if chief is not None else None

    def set_chief(self, name: str, *, only_if_unset: bool = False) -> None:
        self._set_setting("chief", "bot:" + name, only_if_unset=only_if_unset)

    def coordinators(self) -> list[str] | None:
        names = self._setting("coordinators")
        return json.loads(names) if names is not None else None

    def set_coordinators(self, names: list[str], *, only_if_unset: bool = False) -> None:
        self._set_setting("coordinators", json.dumps(names), only_if_unset=only_if_unset)
