"""What bots ask the user to allow (runner/permissions.py), and the answers: what's allowed is the task's grant."""

from typing import Any

from crewbots.store.records import _now


class Permissions:
    """The part of Store for permission requests."""

    def ask_permission(self, task_id: str, kind: str, folder: str, what: str, why: str) -> dict[str, Any]:
        cur = self.db.execute("insert into permissions (task_id, kind, folder, what, why, asked_at) values (?, ?, ?, ?, ?, ?)",
                              (task_id, kind, folder, what, why, _now()))
        return self.permission(cur.lastrowid)

    def permission(self, permission_id: int) -> dict[str, Any]:
        row = self.db.execute("select * from permissions where id = ?", (permission_id,)).fetchone()
        if row is None:
            raise KeyError(f"no permission request {permission_id}")
        return dict(row)

    def answer_permission(self, permission_id: int, allowed: bool) -> dict[str, Any]:
        """Records the user's answer, once: a request that's been answered keeps its answer."""
        self.db.execute("update permissions set allowed = ?, answered_at = ? where id = ? and allowed is null",
                        (int(allowed), _now(), permission_id))
        return self.permission(permission_id)

    def grants(self, task_id: str) -> list[dict[str, Any]]:
        """What the user has allowed the task beyond its role card (folders, the network), oldest first."""
        return [dict(r) for r in self.db.execute(
            "select * from permissions where task_id = ? and allowed = 1 and kind != 'action' order by id",
            (task_id,)).fetchall()]
