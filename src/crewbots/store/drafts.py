"""Code review's records: the line comments drafted for a pull request, and its reviews."""

from typing import Any

from crewbots.store.records import Task, _now, _task


class Drafts:
    """The part of Store for code review's drafts and reviews."""

    def reviews_of(self, pr: str) -> list[Task]:
        rows = self.db.execute("select * from tasks where pr = ? order by created_at", (pr,)).fetchall()
        return [_task(r) for r in rows]

    def add_draft(self, repo: str, number: int, path: str, line: int, side: str, body: str, author: str,
                  task_id: str | None = None, commit_sha: str | None = None) -> dict[str, Any]:
        now = _now()
        cursor = self.db.execute(
            "insert into review_drafts (repo, number, path, line, side, body, author, task_id, commit_sha, created_at,"
            " updated_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (repo, number, path, line, side, body, author, task_id, commit_sha, now, now))
        return self.draft(cursor.lastrowid)

    def draft(self, draft_id: int) -> dict[str, Any]:
        row = self.db.execute("select * from review_drafts where id = ?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return dict(row)

    def drafts(self, repo: str, number: int) -> list[dict[str, Any]]:
        rows = self.db.execute("select * from review_drafts where repo = ? and number = ? order by path, line, id",
                               (repo, number)).fetchall()
        return [dict(r) for r in rows]

    def edit_draft(self, draft_id: int, body: str) -> dict[str, Any]:
        self.draft(draft_id)
        self.db.execute("update review_drafts set body = ?, updated_at = ? where id = ?", (body, _now(), draft_id))
        return self.draft(draft_id)

    def delete_drafts(self, ids: list[int]) -> None:
        self.db.executemany("delete from review_drafts where id = ?", [(i,) for i in ids])

    def reviewed_prs(self) -> dict[str, str]:
        """Each pull request that has a review, with when it was last touched."""
        rows = self.db.execute("select pr, max(updated_at) as at from tasks where pr is not null group by pr").fetchall()
        return {r["pr"]: r["at"] for r in rows}
