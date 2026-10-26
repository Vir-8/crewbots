"""Full-text search over what the crew said and decided: the `recall` tool and /v1/search.

Two indexes of the same rows: `search` matches words, stemmed, and `trigrams` any run of three or more characters,
for what words miss: ids, paths, and parts of code names. Matches rank by how well they match, then a task's title or
a handoff above chatter, and the newer above the older.
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

SEARCHABLE = {"message", "text", "handoff", "review", "escalation", "merge_proposed"}

SNIPPET_TOKENS = 18
# What a kind of row is worth beside a message: a task's title says what it's about, a handoff what came of it.
KIND_WEIGHT = {"task": 2.0, "handoff": 1.6, "review": 1.3, "escalation": 1.3, "merge_proposed": 1.3}
AGE_DAYS = 90.0  # a match this old counts half as much as one from today
# A piece of a query that's an id, a path or a code name rather than a word: it has punctuation inside, a capital
# inside, or letters and digits together.
ID_LIKE = re.compile(r"^(?=.{3,}$)(?:.*[_./#:\-()\[\]].*|.+[A-Z].*|(?=.*\d)(?=.*[A-Za-z]).*)$")


class Search:
    """The part of Store for full-text search over the crew's record."""

    def search(self, query: str, limit: int = 20, exclude_task: str | None = None) -> list[dict[str, Any]]:
        """What the crew said or decided that matches `query`, best first. Ids, paths and code names in it (or, when
        the words match nothing, the words themselves) are matched anywhere in the text; then the words: all of them
        if anything has them all, otherwise any of them."""
        pieces = [p.strip("\"'`,;!?") for p in query.split()]
        ids = [p for p in pieces if ID_LIKE.match(p)]
        found = self._ranked("trigrams", " ".join(_quoted(p) for p in ids), limit, exclude_task) if ids else []
        words = re.findall(r"\w+", query.lower())
        for joiner in (" ", " OR ") if words else ():
            rows = self._ranked("search", joiner.join(f'"{w}"' for w in words), limit * 10 if joiner == " OR " else limit,
                                exclude_task)
            if rows:
                found += _by_coverage(rows, words) if joiner == " OR " else rows
                break
        else:
            if not found and (longer := [w for w in words if len(w) >= 3]):  # parts of words, say "MemoryErr"
                found = self._ranked("trigrams", " OR ".join(_quoted(w) for w in longer), limit, exclude_task)
        seen: set[tuple[str, str]] = set()
        best = [row for row in found if (key := (row["task_id"], row["snippet"])) not in seen and not seen.add(key)]
        return [{k: v for k, v in row.items() if k != "text"} for row in best[:limit]]

    def _ranked(self, table: str, match: str, limit: int, exclude_task: str | None) -> list[dict[str, Any]]:
        weight = " ".join(f"when '{kind}' then {w}" for kind, w in KIND_WEIGHT.items())
        rows = self.db.execute(
            f"select s.task_id, s.kind, s.at, s.text, snippet({table}, 0, '«', '»', '…', {SNIPPET_TOKENS}) as snippet,"
            f" t.bot, t.title, bm25({table}) * (case s.kind {weight} else 1.0 end)"
            f" / (1.0 + max(0.0, julianday(?) - julianday(s.at)) / {AGE_DAYS}) as score"
            f" from {table} s join tasks t on t.id = s.task_id"
            f" where {table} match ? and s.task_id != ? order by score limit ?",
            (datetime.now(UTC).isoformat(), match, exclude_task or "", limit),
        ).fetchall()
        return [{k: r[k] for k in ("task_id", "kind", "at", "snippet", "bot", "title", "text")} for r in rows]

    def _add_search_row(self, text: str, task_id: str, event_id: int, kind: str, at: str) -> None:
        for table in ("search", "trigrams"):
            self.db.execute(f"insert into {table} (text, task_id, event_id, kind, at) values (?, ?, ?, ?, ?)",
                            (text, task_id, event_id, kind, at))

    def _index(self, task_id: str, event_id: int, kind: str, data: dict[str, Any], at: str) -> None:
        if kind not in SEARCHABLE:
            return
        text = _searchable_text(kind, data)
        if text:
            self._add_search_row(text, task_id, event_id, kind, at)

    def _index_history(self) -> None:
        for row in self.db.execute("select id, title, created_at from tasks").fetchall():
            self._add_search_row(row["title"], row["id"], 0, "task", row["created_at"])
        for row in self.db.execute("select * from events").fetchall():
            self._index(row["task_id"], row["id"], row["kind"], json.loads(row["data"]), row["at"])

    def log_recall(self, task_id: str, bot: str, query: str, hits: list[str]) -> None:
        self.db.execute("insert into recall_log (at, task_id, bot, query, hits) values (?, ?, ?, ?, ?)",
                        (datetime.now(UTC).isoformat(timespec="milliseconds"), task_id, bot, query, json.dumps(hits)))

    def recall_log(self, limit: int = 200) -> list[dict[str, Any]]:
        """The queries bots asked `recall`, newest first, with what came back."""
        rows = self.db.execute("select * from recall_log order by id desc limit ?", (limit,)).fetchall()
        return [{**dict(r), "hits": json.loads(r["hits"])} for r in rows]


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


def _quoted(piece: str) -> str:
    return '"' + piece.replace('"', '""') + '"'


def _by_coverage(rows: list[dict[str, Any]], words: list[str]) -> list[dict[str, Any]]:
    """Rows that each match some of the words, by how many of the words their task's rows cover between them, then by
    score: a task that has all the words, if spread over its messages, comes before one with a lucky few."""
    stems = {w: w[:max(4, len(w) - 2)] for w in words}  # near enough to the stemmer: cache, cached; limit, limiting
    covered: dict[str, set[str]] = {}
    for row in rows:
        text = row["text"].lower()
        covered.setdefault(row["task_id"], set()).update(w for w, stem in stems.items() if stem in text)
    return sorted(rows, key=lambda row: -len(covered[row["task_id"]]))  # stable: the score breaks ties
