"""Full-text search over what the crew said and decided: the `recall` tool and /v1/search."""

import json
import re
from typing import Any

SEARCHABLE = {"message", "text", "handoff", "review", "escalation", "merge_proposed"}

SNIPPET_TOKENS = 18


class Search:
    """The part of Store for full-text search over the crew's record."""

    def search(self, query: str, limit: int = 20, exclude_task: str | None = None) -> list[dict[str, Any]]:
        """What the crew said or decided that matches `query`, best first: all the words if anything has them all,
        otherwise any of them."""
        words = re.findall(r"\w+", query.lower())
        if not words:
            return []
        for joiner in (" ", " OR "):
            match = joiner.join(f'"{w}"' for w in words)
            rows = self.db.execute(
                f"select s.task_id, s.kind, s.at, snippet(search, 0, '«', '»', '…', {SNIPPET_TOKENS}) as snippet,"
                " t.bot, t.title from search s join tasks t on t.id = s.task_id"
                " where search match ? and s.task_id != ? order by bm25(search) limit ?",
                (match, exclude_task or "", limit),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
        return []

    def _index(self, task_id: str, event_id: int, kind: str, data: dict[str, Any], at: str) -> None:
        if kind not in SEARCHABLE:
            return
        text = _searchable_text(kind, data)
        if text:
            self.db.execute("insert into search (text, task_id, event_id, kind, at) values (?, ?, ?, ?, ?)",
                            (text, task_id, event_id, kind, at))

    def _index_history(self) -> None:
        for row in self.db.execute("select id, title, created_at from tasks").fetchall():
            self.db.execute("insert into search (text, task_id, event_id, kind, at) values (?, ?, 0, 'task', ?)",
                            (row["title"], row["id"], row["created_at"]))
        for row in self.db.execute("select * from events").fetchall():
            self._index(row["task_id"], row["id"], row["kind"], json.loads(row["data"]), row["at"])


def _searchable_text(kind: str, data: dict[str, Any]) -> str:
    match kind:
        case "message" | "text":
            return str(data.get("text", ""))
        case "handoff":
            return " ".join(str(data.get(k, "")) for k in ("done", "open", "needs")) + " " + " ".join(data.get("output") or [])
        case "review":
            findings = " ".join(f"{f.get('location', '')} {f.get('problem', '')}" for f in data.get("findings") or [])
            return f"{data.get('verdict', '')} {data.get('summary', '')} {findings}"
        case "escalation":
            return str(data.get("question", ""))
        case "merge_proposed":
            return str(data.get("summary", ""))
    return ""
