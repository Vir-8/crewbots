"""Tasks: making and reading them, their tree, their handoffs and verdicts, and the turns under way."""

import json
import uuid
from typing import Any

from crewbots.store.records import STOPPED, Handoff, Review, Status, Task, Verdict, _now, _review, _task

TITLE_CHARS = 80

# A task and everything it delegated, however deep: the ids, for `id in (...)`.
TREE = ("with recursive tree(id) as (select ? union select t.id from tasks t join tree on t.parent_id = tree.id)"
        " select id from tree")


class Tasks:
    """The part of Store for tasks, their tree, handoffs and verdicts, and turns under way."""

    def create_task(
        self, bot: str, goal: str, *, created_by: str = "user", parent_id: str | None = None, workspace: str | None = None,
        review_of: str | None = None, review_version: int | None = None, routine: str | None = None,
        budget: int | None = None, title: str | None = None, kind: str = "thread", room_id: str | None = None,
        status: "Status | None" = None, pr: str | None = None,
    ) -> Task:
        task_id = f"t_{uuid.uuid4().hex[:10]}"
        now = _now()
        first_line = title or (goal.strip().splitlines()[0] if goal.strip() else "(empty)")
        title = first_line if len(first_line) <= TITLE_CHARS else first_line[: TITLE_CHARS - 1] + "…"
        self.db.execute(
            "insert into tasks (id, bot, title, status, created_by, parent_id, workspace, review_of, review_version,"
            " routine, budget, kind, room_id, pr, created_at, updated_at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, bot, title, status or Status.QUEUED, created_by, parent_id, workspace, review_of, review_version,
             routine, budget, kind, room_id, pr, now, now),
        )
        self._add_search_row(title, task_id, 0, "task", now)
        task = self.get_task(task_id)
        if task.status in STOPPED:
            self._stopped_event(task)
        return task

    def get_task(self, task_id: str) -> Task:
        row = self.db.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return _task(row)

    def list_tasks(self, status: Status | None = None, bot: str | None = None, limit: int = 100) -> list[Task]:
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if bot:
            where.append("bot = ?")
            args.append(bot)
        sql = "select * from tasks" + (" where " + " and ".join(where) if where else "")
        rows = self.db.execute(sql + " order by created_at desc, id desc limit ?", (*args, limit)).fetchall()
        return [_task(r) for r in rows]

    def children(self, parent_id: str) -> list[Task]:
        rows = self.db.execute("select * from tasks where parent_id = ? order by created_at, id", (parent_id,)).fetchall()
        return [_task(r) for r in rows]

    def delegated_by(self, bot: str) -> list[Task]:
        rows = self.db.execute(
            "select t.* from tasks t join tasks p on p.id = t.parent_id "
            "where p.bot = ? and (t.status in ('queued', 'running', 'held', 'needs_input', 'interrupted') "
            "or t.id in (select recent.id from tasks recent join tasks parent on parent.id = recent.parent_id "
            "where parent.bot = ? and recent.status not in ('queued', 'running', 'held', 'needs_input', 'interrupted') "
            "order by recent.updated_at desc, recent.id desc limit 20)) order by t.created_at, t.id", (bot, bot)).fetchall()
        return [_task(r) for r in rows]

    def last_activity(self, bot: str) -> str | None:
        """When any task of the bot's last changed."""
        return self.db.execute("select max(updated_at) from tasks where bot = ?", (bot,)).fetchone()[0]

    def has_pending_permission(self, task_id: str) -> bool:
        """Whether the task asked the user for a permission that is still unanswered."""
        return bool(self.db.execute(
            "select 1 from permissions p where p.task_id = ? and p.allowed is null and not exists (select 1 from events c "
            "where c.task_id = p.task_id and c.kind = 'questions_closed' and c.at >= p.asked_at)", (task_id,)).fetchone())

    def owed_by_delegator(self, bot: str) -> list[Task]:
        """Code tasks other bots finished for `bot` that it still owes an action on: idle, and the latest of their handoff,
        review verdict, merge, withdrawal and drop is a handoff or a verdict (one that failed is the delegator's to send
        back; the other three close it). One waiting on the user to merge it isn't owed, nor one dropped on purpose."""
        rows = self.db.execute(
            "select t.* from tasks t join tasks p on p.id = t.parent_id where p.bot = ? and t.bot != p.bot "
            "and t.status = 'idle' and t.branch is not null and t.review_of is null "
            "and coalesce(t.merge_state, '') not in ('merged', 'merging', 'proposed', 'dropped') and (select e.kind || ':' || "
            "coalesce(json_extract(e.data, '$.verdict'), '') from events e where e.task_id = t.id and e.kind in "
            "('handoff', 'review', 'merged', 'merge_withdrawn', 'worktree_dropped') order by e.id desc limit 1) "
            "in ('handoff:', 'review:pass', 'review:fail') order by t.created_at, t.id", (bot,)).fetchall()
        return [_task(r) for r in rows]

    def asking_delegator(self, bot: str) -> list[Task]:
        """Threads other bots are doing for `bot` that wait on its answer to a question they escalated, rather than on
        the user's permission."""
        rows = self.db.execute(
            "select t.* from tasks t join tasks p on p.id = t.parent_id where p.bot = ? and t.bot != p.bot "
            "and t.status = 'needs_input' and t.kind = 'thread' order by t.created_at, t.id", (bot,)).fetchall()
        return [_task(r) for r in rows if not self.has_pending_permission(r["id"])]

    def stuck_tasks(self, bot: str) -> list[Task]:
        """Tasks of the bot's own that wait to run: held, queued, or interrupted by a daemon stop."""
        rows = self.db.execute("select * from tasks where bot = ? and status in ('held', 'queued', 'interrupted') "
                               "and kind not in ('chat', 'seat') order by created_at, id", (bot,)).fetchall()
        return [_task(r) for r in rows]

    def unfinished_children(self, parent_id: str) -> int:
        """Children still running, waiting to run, held, or waiting for an answer."""
        return self.db.execute(
            "select count(*) from tasks where parent_id = ? and status in (?, ?, ?, ?)",
            (parent_id, Status.QUEUED, Status.RUNNING, Status.HELD, Status.NEEDS_INPUT)).fetchone()[0]

    def add_handoff(self, task_id: str, from_bot: str, done: str, output: list[str], open: str, needs: str,
                    evidence: list[str] | None = None, checks: list[str] | None = None) -> Handoff:
        now = _now()
        cur = self.db.execute(
            "insert into handoffs (task_id, from_bot, done, output, open, needs, at, evidence, checks)"
            " values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, from_bot, done, json.dumps(output), open, needs, now, json.dumps(evidence or []),
             json.dumps(checks or [])),
        )
        return Handoff(id=cur.lastrowid, task_id=task_id, from_bot=from_bot, done=done, output=output, open=open,
                       needs=needs, at=now, evidence=list(evidence or []), checks=list(checks or []))

    def latest_handoff(self, task_id: str) -> Handoff | None:
        row = self.db.execute("select * from handoffs where task_id = ? order by id desc limit 1", (task_id,)).fetchone()
        if row is None:
            return None
        return Handoff(**{**dict(row), "output": json.loads(row["output"]), "evidence": json.loads(row["evidence"]),
                          "checks": json.loads(row["checks"])})

    def merged_tasks(self, limit: int = 50) -> list[tuple[Task, str, dict[str, Any]]]:
        """Merged work, the latest merge first: each task, when it merged, and its `merged` event's data."""
        rows = self.db.execute(
            "select e.at as merged_at, e.data as merged, t.id from tasks t join events e on e.id = (select max(id) from "
            "events where task_id = t.id and kind = 'merged') where t.merge_state = 'merged' order by e.id desc limit ?",
            (limit,)).fetchall()
        return [(self.get_task(r["id"]), r["merged_at"], json.loads(r["merged"])) for r in rows]

    def reviewers_of(self, work_id: str) -> list[Task]:
        """The review tasks of a piece of work, oldest first."""
        rows = self.db.execute("select * from tasks where review_of = ? order by created_at, id", (work_id,)).fetchall()
        return [_task(r) for r in rows]

    def add_review(self, task_id: str, work_id: str, reviewer: str, version: int, verdict: Verdict, summary: str,
                   findings: list[dict[str, str]]) -> Review:
        now = _now()
        cur = self.db.execute(
            "insert into reviews (task_id, work_id, reviewer, version, verdict, summary, findings, at)"
            " values (?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, work_id, reviewer, version, verdict, summary, json.dumps(findings), now),
        )
        return Review(id=cur.lastrowid, task_id=task_id, work_id=work_id, reviewer=reviewer, version=version,
                      verdict=verdict, summary=summary, findings=findings, at=now)

    def latest_review(self, task_id: str) -> Review | None:
        """The verdict a review task gave, if it gave one; a later verdict replaces an earlier one."""
        row = self.db.execute("select * from reviews where task_id = ? order by id desc limit 1", (task_id,)).fetchone()
        return _review(row) if row else None

    def update_task(self, task_id: str, **fields: Any) -> Task:
        allowed = set(Task.__dataclass_fields__) - {"id", "bot", "created_by", "created_at", "updated_at"}
        if bad := set(fields) - allowed:
            raise ValueError(f"cannot update {sorted(bad)}")  # the owner, in particular, never changes here
        fields["updated_at"] = _now()
        before = self.get_task(task_id).status if "status" in fields else None
        assignments = ", ".join(f"{k} = ?" for k in fields)
        cur = self.db.execute(f"update tasks set {assignments} where id = ?", (*fields.values(), task_id))
        if cur.rowcount == 0:
            raise KeyError(task_id)
        task = self.get_task(task_id)
        if before is not None and before not in STOPPED and task.status in STOPPED:
            self._stopped_event(task)
        return task

    def _stopped_event(self, task: Task) -> None:
        """A task stopping is a crew event, so that whoever looks around finds it by id, however its time ties."""
        self.add_crew_event("task_stopped", task.id, f"{task.bot}'s {task.title!r} is {task.status.value.replace('_', ' ')}")

    def add_usage(self, task_id: str, input_tokens: int, output_tokens: int, cost_usd: float | None) -> None:
        self.db.execute(
            "update tasks set input_tokens = input_tokens + ?, output_tokens = output_tokens + ?,"
            " cost_usd = cost_usd + ? where id = ?",
            (input_tokens, output_tokens, cost_usd or 0.0, task_id),
        )

    def mark_interrupted(self) -> list[str]:
        """On startup: anything still marked running or queued was cut off when the daemon last stopped."""
        rows = self.db.execute("select id from tasks where status in (?, ?, ?)",
                               (Status.RUNNING, Status.QUEUED, Status.HELD)).fetchall()
        for row in rows:
            self.update_task(row["id"], status=Status.INTERRUPTED, error="the daemon stopped during this task")
        return [r["id"] for r in rows]

    def set_pending(self, task_id: str, prompt: str, resumed: bool = False) -> None:
        self.db.execute("insert or replace into pending_turns (task_id, prompt, resumed) values (?, ?, ?)",
                        (task_id, prompt, int(resumed)))

    def pending_started(self, task_id: str) -> None:
        self.db.execute("update pending_turns set started = 1 where task_id = ?", (task_id,))

    def pending_waiting(self, task_id: str, text: str) -> None:
        self.db.execute("update pending_turns set waiting = json_insert(waiting, '$[#]', ?) where task_id = ?",
                        (text, task_id))

    def pending_steered(self, task_id: str, text: str) -> None:
        """A message steered into the turn under way is part of what it was asked."""
        self.db.execute("update pending_turns set prompt = prompt || char(10) || char(10) || ? where task_id = ?",
                        (text, task_id))

    def primed(self, task_id: str) -> dict[str, str] | None:
        """What the task's session started with, part by part (see `set_primed`); None if it isn't known."""
        row = self.db.execute("select parts from primed where task_id = ?", (task_id,)).fetchone()
        return json.loads(row["parts"]) if row else None

    def set_primed(self, task_id: str, parts: dict[str, str]) -> None:
        self.db.execute("insert into primed (task_id, parts) values (?, ?) on conflict(task_id) do update set "
                        "parts = excluded.parts", (task_id, json.dumps(parts)))

    def clear_pending(self, task_id: str) -> None:
        self.db.execute("delete from pending_turns where task_id = ?", (task_id,))

    def interrupted_turns(self) -> list[dict[str, Any]]:
        """The turns of interrupted tasks, oldest first."""
        rows = self.db.execute(
            "select p.* from pending_turns p join tasks t on t.id = p.task_id where t.status = ? order by t.updated_at",
            (Status.INTERRUPTED,)).fetchall()
        return [{**dict(r), "started": bool(r["started"]), "resumed": bool(r["resumed"]),
                 "waiting": json.loads(r["waiting"])} for r in rows]

    def work_of(self, task_id: str) -> Task:
        """The piece of work a task belongs to: the outermost thread above it (or itself), stopping at a chat or seat.
        A budget or a release covers that much, not a bot's whole never-ending chat."""
        task = self.get_task(task_id)
        while task.parent_id:
            parent = self.get_task(task.parent_id)
            if parent.kind != "thread":
                break
            task = parent
        return task

    def root_of(self, task_id: str) -> Task:
        task = self.get_task(task_id)
        while task.parent_id:
            task = self.get_task(task.parent_id)
        return task

    def delegated_out(self, bot: str) -> int:
        """Tasks `bot` handed to another bot that haven't finished: running, waiting, held, or asking a question."""
        return self.db.execute(
            "select count(*) from tasks where created_by = ? and bot != ? and status in (?, ?, ?, ?)",
            (bot, bot, Status.QUEUED, Status.RUNNING, Status.HELD, Status.NEEDS_INPUT)).fetchone()[0]

    def chat_of(self, bot: str) -> Task | None:
        row = self.db.execute("select * from tasks where kind = 'chat' and bot = ? order by created_at limit 1",
                              (bot,)).fetchone()
        return _task(row) if row else None

    def started_on_own(self, bot: str, chat_id: str, since: str) -> int:
        """How many threads `bot` started on its own (from a check-in, under its chat) since `since`."""
        return self.db.execute("select count(*) from tasks where bot = ? and parent_id = ? and created_by = 'crewbots'"
                               " and created_at >= ?", (bot, chat_id, since)).fetchone()[0]

    def seats_in(self, room_id: str) -> list[Task]:
        rows = self.db.execute("select * from tasks where kind = 'seat' and room_id = ?", (room_id,)).fetchall()
        return [_task(r) for r in rows]

    def seats_of(self, bot: str) -> list[Task]:
        rows = self.db.execute("select * from tasks where kind = 'seat' and bot = ? order by id", (bot,)).fetchall()
        return [_task(r) for r in rows]

    def seat_of(self, room_id: str, bot: str) -> Task | None:
        row = self.db.execute("select * from tasks where kind = 'seat' and room_id = ? and bot = ?",
                              (room_id, bot)).fetchone()
        return _task(row) if row else None

    def threads_of(self, bot: str, limit: int = 50) -> list[Task]:
        """A bot's work, newest first. Code reviews are kept apart, with their pull requests."""
        rows = self.db.execute("select * from tasks where kind = 'thread' and pr is null and bot = ?"
                               " order by updated_at desc limit ?", (bot, limit)).fetchall()
        return [_task(r) for r in rows]

    def tree(self, root_id: str) -> list[Task]:
        rows = self.db.execute(f"select * from tasks where id in ({TREE}) order by created_at, id", (root_id,)).fetchall()
        return [_task(r) for r in rows]
