"""Routines' bookkeeping: when each was last checked and run, and what each trigger has started on."""

from typing import Any

from crewbots.store.records import _now


class Routines:
    """The part of Store for routines' bookkeeping."""

    def routine_state(self, name: str) -> dict[str, Any] | None:
        row = self.db.execute("select * from routines where name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def routine_checked(self, name: str, at: str) -> None:
        self.db.execute("insert into routines (name, checked_at) values (?, ?) on conflict(name) do update set"
                        " checked_at = excluded.checked_at", (name, at))

    def routine_ran(self, name: str, at: str, task_id: str) -> None:
        self.db.execute("insert into routines (name, checked_at, last_run_at, last_task_id) values (?, ?, ?, ?)"
                        " on conflict(name) do update set last_run_at = excluded.last_run_at,"
                        " last_task_id = excluded.last_task_id", (name, at, at, task_id))

    def triggered(self, routine: str, keys: list[str]) -> set[str]:
        """Which of `keys` this trigger already started on."""
        if not keys:
            return set()
        rows = self.db.execute(f"select key from triggered where routine = ? and key in ({','.join('?' * len(keys))})",
                               (routine, *keys)).fetchall()
        return {r["key"] for r in rows}

    def mark_triggered(self, routine: str, keys: list[str]) -> None:
        now = _now()
        self.db.executemany("insert or ignore into triggered (routine, key, at) values (?, ?, ?)",
                            [(routine, k, now) for k in keys])
