"""Everything the crew has done, in one SQLite file: tasks, and every event of every run.

A task is the unit of work and of conversation. It has exactly one owner bot, its own backend session (so a follow-up
message resumes where it left off), optionally its own worktree, and a transcript made of its events. There are three
kinds:

- a **chat**: a bot's one ongoing conversation with the user, which never ends;
- a **seat**: a bot's own conversation inside a group chat (a room), fed the room's new messages;
- a **thread**: a piece of work, opened from a chat, a seat, a routine or another thread.

A room is a group chat: the user and some bots, with one of them the lead. Its messages are kept on their own.

Store's parts are by area: tasks, events, rooms, search, code review's drafts, routines, what happened around the
crew, and what bots asked the user to allow; `db.py` has the database itself and its migrations.
"""

import json
from pathlib import Path

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

    def auto_merge(self) -> bool:
        row = self.db.execute("select value from crew_settings where key = 'auto_merge'").fetchone()
        return bool(row and row[0] == 1)

    def set_auto_merge(self, enabled: bool) -> None:
        self.db.execute("insert into crew_settings (key, value) values ('auto_merge', ?) "
                        "on conflict(key) do update set value = excluded.value", (int(enabled),))

    def stall_minutes(self) -> int:
        """How long a bot with open work may sit idle before the daemon wakes it."""
        row = self.db.execute("select value from crew_settings where key = 'stall_minutes'").fetchone()
        return int(row[0]) if row and int(row[0]) > 0 else 15

    def set_stall_minutes(self, minutes: int) -> None:
        self.db.execute("insert into crew_settings (key, value) values ('stall_minutes', ?) "
                        "on conflict(key) do update set value = excluded.value", (int(minutes),))

    def timezone(self) -> str | None:
        """The crew's chosen time zone; None until one is chosen, when the machine's own is used."""
        row = self.db.execute("select value from crew_settings where key = 'timezone'").fetchone()
        return str(row[0]).removeprefix("zone:") if row else None

    def set_timezone(self, name: str | None) -> None:
        if name is None:
            self.db.execute("delete from crew_settings where key = 'timezone'")
        else:  # the value column has INTEGER affinity; the prefix keeps any name as text
            self.db.execute("insert into crew_settings (key, value) values ('timezone', ?) "
                            "on conflict(key) do update set value = excluded.value", ("zone:" + name,))
        timefmt.set_zone(name)

    def chief(self) -> str | None:
        row = self.db.execute("select value from crew_settings where key = 'chief'").fetchone()
        return str(row[0]).removeprefix("bot:") if row else None

    def set_chief(self, name: str, *, only_if_unset: bool = False) -> None:
        conflict = "do nothing" if only_if_unset else "do update set value = excluded.value"
        # The existing value column has INTEGER affinity; the prefix preserves handles such as "001".
        self.db.execute(f"insert into crew_settings (key, value) values ('chief', ?) on conflict(key) {conflict}",
                        ("bot:" + name,))

    def coordinators(self) -> list[str] | None:
        row = self.db.execute("select value from crew_settings where key = 'coordinators'").fetchone()
        return json.loads(row[0]) if row else None

    def set_coordinators(self, names: list[str], *, only_if_unset: bool = False) -> None:
        conflict = "do nothing" if only_if_unset else "do update set value = excluded.value"
        self.db.execute(f"insert into crew_settings (key, value) values ('coordinators', ?) on conflict(key) {conflict}",
                        (json.dumps(names),))
