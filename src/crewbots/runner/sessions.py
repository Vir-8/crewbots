"""Long-lived sessions: one per active task, opened when needed, reused across turns, closed when idle; told what
changed in their instructions; and, as a conversation grows long, flushed to memory and compacted in place (or, when
it sat idle past its cache, carried on in a fresh session from memory).

A bot's chat and its seats in group chats share one session, its chat's: it's one bot wherever it talks, with one
memory of the day, not a stranger in each room. Each turn says where it is, and its reply goes back there."""

import asyncio
import contextlib
import hashlib
import logging
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path
from typing import Any

from crewbots import prompts, timefmt, working_state, worktree
from crewbots.backends import Backend
from crewbots.crew import NO_REPLY, Bot, is_no_reply
from crewbots.events import Compacted, Done, Event, Usage
from crewbots.runner.common import NotAllowed, OpenSession, _plan, _usage_data
from crewbots.store import Status, Task

MIN_KEPT = 500  # tokens: a compaction that leaves fewer kept nothing worth carrying on from
COLD_PERCENT = 30.0  # of its window: an idle conversation this full starts afresh once its cache has gone
FLUSH_LINES = 12  # a flush's lines for the daily log, at most
FRESH_MESSAGES = 8  # of the conversation a fresh session starts with
MESSAGE_CHARS = 1500  # of each
ROOM_LATELY = 8  # messages of a group chat a session is shown when it first talks there
ROOM_CHARS = 600  # of each
# A bot's chat and group chats are woken often, and each turn reads the whole conversation back, so they hold no more
# than this, compacted at the runner's rollover_percent of it (about 120K); what's folded away stays findable with
# `recall`.
CONVERSATION_WINDOW = 170_000


class Sessions:
    """The part of Runner that keeps sessions open, reuses them, and keeps a long one in shape."""

    def open_sessions(self) -> list[str]:
        return list(self._sessions)

    async def status(self, task_id: str) -> dict[str, Any]:
        """What `/status` shows for a chat or thread: its bot's model and effort, its session, and how full the
        session's context is: measured now if the session is open and idle, else as last measured."""
        task = self.store.get_task(task_id)
        bot = self.bot_for(task.bot)
        mind = self._mind(task)
        opened = self._sessions.get(mind.id)
        percent = self._context_seen.get(mind.id)
        measure = getattr(opened.session, "context_percent", None) if opened and opened.session.alive else None
        if measure and not self._session_busy(mind.id):
            with contextlib.suppress(Exception):
                percent = await asyncio.wait_for(measure(), 10) or percent
                if percent is not None:
                    self._context_seen[mind.id] = percent
        return {
            "task_id": task.id, "bot": task.bot, "backend": bot.backend if bot else None,
            "plan": _plan(bot.backend) if bot else None, "model": bot.model if bot else None,
            "effort": bot.effort if bot else None, "session_id": mind.session_id, "open": opened is not None,
            "context_percent": round(percent, 1) if percent is not None else None,
            "context_window": self._window(task, bot) if bot and task.kind != "review" else None,
            "rollover_percent": self.rollover_percent, "turns": task.turns,
            "background": self.capacity.verdict(_plan(bot.backend)).reason if bot else None,
        }

    def _window(self, task: Task, bot: Bot) -> int | None:
        """The context a task's sessions hold before compacting: its bot's, and at most CONVERSATION_WINDOW for a chat
        or group chat."""
        if task.kind in ("chat", "seat"):
            return min(bot.context_window or CONVERSATION_WINDOW, CONVERSATION_WINDOW)
        return bot.context_window

    def _mind(self, task: Task) -> Task:
        """The task whose session a turn runs in: a seat's is its bot's chat; any other task's is its own."""
        return self.chat(task.bot) if task.kind == "seat" else task

    def _mind_lock(self, task: Task) -> contextlib.AbstractAsyncContextManager[Any]:
        """One turn at a time in a shared session: a seat's turn waits for its chat's, and the other way round."""
        if task.kind not in ("chat", "seat"):
            return contextlib.nullcontext()
        return self.__dict__.setdefault("_mind_locks", {}).setdefault(self._mind(task).id, asyncio.Lock())

    def _session_busy(self, key: str) -> bool:
        """Whether the session kept under `key` is taking a turn, its own or a seat's."""
        lock = self.__dict__.get("_mind_locks", {}).get(key)
        return key in self._runs or bool(lock and lock.locked())

    def _grants(self, task: Task) -> list[dict[str, Any]]:
        """What the user allowed the task beyond its bot's role card; for a shared session, what they allowed in any of
        its conversations."""
        if task.kind not in ("chat", "seat"):
            return self.store.grants(task.id)
        return [g for t in (self._mind(task), *self.store.seats_of(task.bot)) for g in self.store.grants(t.id)]

    def _channel(self, task: Task) -> str:
        """Where a turn in a shared session is, and where its reply goes, as the line its message starts with."""
        if task.kind == "seat" and task.room_id:
            title = " ".join(self.store.room(task.room_id)["title"].split())
            return f"[In the group chat {title!r} ({task.room_id}); your reply is posted there]\n"
        if task.kind == "chat" and self.store.seats_of(task.bot):  # a bot in no group chat has nothing to tell apart
            return "[In your chat with the user; your reply goes to them]\n"
        return ""

    async def _stop_session(self, task_id: str) -> None:
        """After a task's turn was stopped: its session goes, unless it stopped the turn and stays warm, or another
        conversation sharing it has a turn under way."""
        key = self._mind(self.store.get_task(task_id)).id
        opened = self._sessions.get(key)
        warm = opened and opened.session.alive and getattr(opened.session, "interruptible", False)
        if not warm and not self._session_busy(key):
            await self._close_session(key)

    async def _session_for(self, task: Task, bot: Bot, cwd: Path, backend: Backend) -> OpenSession | None:
        """The task's open session (for a seat, its chat's), or a new one; None to run this turn as its own process."""
        mind = self._mind(task)
        current = self._sessions.get(mind.id)
        grants = tuple(g["id"] for g in self._grants(task))
        deny_read = tuple(self._state_denials(bot.name))
        coordinator = self.is_coordinator(bot.name)
        chief = self.is_chief(bot.name)
        if (current and current.session.alive and current.cwd == cwd and current.grants == grants
                and current.deny_read == deny_read and current.coordinator == coordinator and current.chief == chief):
            return current  # one that opened before the user allowed something opens again, with it
        if current:
            await self._close_session(mind.id)
        if not self.use_sessions or not hasattr(backend, "open"):
            return None
        await self._make_room()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = task.id  # its MCP server may call in while the session starts
        try:
            session = await backend.open(self._spec(mind, bot, cwd, token, "", backend))
        except Exception as e:
            self._tokens.pop(token, None)
            self._record(task.id, kind="notice", data={"message": f"couldn't keep a session open ({e}); "
                                                                  "running this turn on its own"})
            return None
        opened = OpenSession(session=session, token=token, cwd=cwd, last_used=asyncio.get_running_loop().time(),
                             grants=grants, deny_read=deny_read, coordinator=coordinator, chief=chief)
        self._sessions[mind.id] = opened
        return opened

    def _refresh_working_state(self, bot: str) -> None:
        try:
            self._sync_working_state(bot)
        except Exception:
            logging.getLogger(__name__).exception("Could not sync working state for %s", bot)

    def _sync_working_state(self, bot: str) -> bool:
        """Refresh metadata without letting a failed state write stop the turn."""
        try:
            db = self.store.db
            pending = list(self._escalated)
            placeholders = ",".join("?" for _ in pending) or "null"
            active = ("t.status in ('idle', 'running', 'queued', 'held', 'needs_input', 'interrupted') "
                      f"or t.id in ({placeholders})")
            escalations = db.execute(
                "select t.id, e.at, json_extract(e.data, '$.question') as question from events e "
                "join tasks t on t.id = e.task_id where t.bot = ? and t.parent_id is null "
                "and e.kind = 'escalation' and t.status not in ('failed', 'cancelled') and (" + active + ") "
                "and not exists (select 1 from events a where a.task_id = t.id and a.id > e.id "
                "and ((a.kind = 'message' and json_extract(a.data, '$.from') = 'user') "
                "or a.kind = 'questions_closed')) order by e.id",
                (bot, *pending)).fetchall()
            permissions = db.execute(
                "select t.id, p.asked_at as at, p.what as question from permissions p join tasks t on t.id = p.task_id "
                "where t.bot = ? and p.allowed is null and t.status not in ('failed', 'cancelled') and (" + active + ") "
                "and not exists (select 1 from events asked join events closed on closed.task_id = asked.task_id "
                "and closed.kind = 'questions_closed' and closed.id > asked.id where asked.task_id = t.id "
                "and asked.kind = 'permission' and json_extract(asked.data, '$.permission') = p.id) "
                "order by p.id", (bot, *pending)).fetchall()
            questions = [f"- {r['id']} · {timefmt.minute(r['at'])} · {' '.join((r['question'] or '').split())[:120]}"
                         for r in sorted([*escalations, *permissions], key=lambda r: r['at'])]
            # Its own work first, what's under way before what's done (merged or dropped work is closed), then its chat
            # and group chats: every line is the bot's own, with who asked for it and where.
            rows = db.execute(
                "select t.id, t.kind, t.status, t.updated_at, t.room_id, t.title, t.created_by, r.title as room, "
                "asked.title as asked_in from tasks t left join rooms r on r.id = t.room_id "
                "left join tasks p on p.id = t.parent_id left join rooms asked on asked.id = p.room_id where t.bot = ? "
                "and ((t.kind = 'chat') or (t.kind = 'seat' and r.id is not null and r.archived_at is null) "
                "or (t.kind not in ('chat', 'seat') "
                "and t.status in ('idle', 'queued', 'running', 'held', 'needs_input', 'interrupted', 'failed') "
                "and coalesce(t.merge_state, '') not in ('merged', 'dropped'))) "
                "order by t.kind in ('chat', 'seat'), t.status = 'idle', t.updated_at desc, t.id limit 12",
                (bot,)).fetchall()
            question_tasks = {r["id"] for r in [*escalations, *permissions]}
            elsewhere = [f"- {_own(r, bot)} · {r['status']} · {timefmt.minute(r['updated_at'])}"
                         + (" · question open" if r["id"] in question_tasks else "")
                         + (f" · {' '.join(r['title'].split())}" if r["kind"] not in ("chat", "seat") else "")
                         for r in rows]
            working_state.sync(self.crew.memory.root / bot / "working-state" / "state.md", questions, elsewhere)
            return True
        except Exception:
            logging.getLogger(__name__).exception("Could not sync working state for %s", bot)
            return False

    def _with_updates(self, task: Task, bot: Bot, prompt: str) -> tuple[str, dict[str, str] | None]:
        """`_updates`, under a first line saying what the time is: in the turn rather than the cached instructions."""
        prompt, primed = self._updates(task, bot, prompt)
        return f"{timefmt.header()}\n\n{prompt}", primed

    def _updates(self, task: Task, bot: Bot, prompt: str) -> tuple[str, dict[str, str] | None]:
        """The turn's message, with what changed in the bot's instructions since its session began put in front of it,
        and what to record as seen once the turn has gone through. A session keeps the instructions it started with
        (both CLIs do), so a changed note, team file or role card reaches it here, in the turn, and the cached start
        of the conversation stays as it was. A chat's or seat's message starts with where it is."""
        if task.kind == "review":  # its own instructions, none of the crew's
            return prompt, None
        prompt = self._channel(task) + prompt
        try:
            synced = self._sync_working_state(bot.name)
        except Exception:
            logging.getLogger(__name__).exception("Could not sync working state for %s", bot.name)
            synced = False
        try:
            living = self.crew.living_parts(bot)
        except Exception:
            logging.getLogger(__name__).exception("Could not prepare instruction updates for %s", bot.name)
            return prompt, None
        authority = prompts.text("coordinating", self.crew.home) if self.is_coordinator(bot.name) else (
            "You are not a configured coordinator. Coordinator-only tools belong to the configured coordinators.")
        authority += "\n\n" + (prompts.text("chief-push", self.crew.home) if self.is_chief(bot.name) else
                                  "You are not the configured chief. Chief-only tools belong to the configured chief.")
        living["chief-role"] = ("Coordinator and chief permissions", authority)
        room = f"room-setting:{task.room_id}"  # a shared session is told each of its group chats' settings
        if task.kind == "seat" and (setting := self._setting(task)):
            living[room] = (f"Group chat setting: {task.room_id}", setting)
        mind = self._mind(task)
        seen = self.store.primed(mind.id) if mind.session_id else None
        if not synced:
            living.pop("working-state", None)
        now = {key: hashlib.sha256(text.encode()).hexdigest()[:16] for key, (_, text) in living.items()}
        if seen and "working-state" not in living and "working-state" in seen:
            now["working-state"] = seen["working-state"]
        now |= {key: value for key, value in (seen or {}).items() if key.startswith("room-setting:") and key not in now}
        lately = self._room_lately(task, prompt) if room in living and room not in (seen or {}) else ""
        if seen is None:
            if mind.session_id:
                keys = [key for key in ("lately", "working-state", room, "chief-role") if key in living]
                updates = "\n\n".join(f"## {living[key][0]}\n\n{living[key][1]}" for key in keys) + lately
                return prompts.text("crew-updates", self.crew.home, updates=updates, message=prompt), now
            if room in living:  # a new session's instructions are its chat's: a group chat's setting comes in the turn
                updates = f"## {living[room][0]}\n\n{living[room][1]}{lately}"
                return prompts.text("crew-updates", self.crew.home, updates=updates, message=prompt), now
            return prompt, now  # a new session starts from these (or one from before they were kept: from now on)
        changed = [key for key in now if seen.get(key) != now[key]] + [key for key in seen if key not in now]
        if not changed:
            return prompt, None
        updates = "\n\n".join(f"## {living[key][0]}\n\n{living[key][1]}" if key in living else f"## {key}\n\n(removed)"
                                for key in changed) + lately
        if not seen and (state := self.crew.memory.state(bot.name, mind.id)):  # just compacted: all of it, again
            updates += f"\n\n## Where this task stands\n\n{state}"
        return prompts.text("crew-updates", self.crew.home, updates=updates, message=prompt), now

    def _room_lately(self, task: Task, prompt: str) -> str:
        """What was said in a seat's group chat before this turn's messages, for a session that hasn't talked there yet
        (or since it was compacted)."""
        through = self._seat_turn_seen.get(task.id) or self.store.seen(f"seat:{task.id}")
        said = [m for m in self.store.recent_room_messages(task.room_id, through, 3 * ROOM_LATELY)
                if f"[{m['author']}]: {m['text']}" not in prompt][-ROOM_LATELY:]
        if not said:
            return ""
        return "\n\n## Lately in this group chat\n\n" + "\n\n".join(
            f"[{m['author']}]: {_short(m['text'], ROOM_CHARS)}" for m in said)

    async def _maybe_compact(self, task_id: str, opened: OpenSession | None, bot: Bot) -> None:
        """A conversation past `rollover_percent` of its context window is compacted at the end of a turn, while its
        cache is warm: its bot first sets down where things stand (`_flush`), then the CLI compacts it in place with
        the crew's instructions. If that fails twice, it carries on in a fresh session from its notes, logs and state.
        A session that can't compact on request rolls over to a fresh one from a briefing instead (`_roll_over`)."""
        measure = getattr(opened.session, "context_percent", None) if opened and opened.session.alive else None
        if not self.rollover_percent or measure is None:
            return
        percent = await measure()
        if percent is not None:
            self._context_seen[task_id] = percent
        if percent is None or percent < self.rollover_percent:
            return
        compact = getattr(opened.session, "compact", None)
        if compact is None:
            await self._roll_over(task_id, opened, bot, percent)
            return
        await self._flush(task_id, opened, bot)
        if ((await measure()) or 0) < self.rollover_percent:
            return  # the CLI compacted it itself, as the flush began
        for _ in range(2):
            done, events = await self._hidden(task_id, compact(prompts.text("compact", self.crew.home)), bot)
            compaction = next((e for e in events if isinstance(e, Compacted)), None)
            if compaction and done and not done.is_error and (compaction.after is None or compaction.after >= MIN_KEPT):
                self._record(task_id, compaction)
                self.store.set_primed(task_id, {})  # its next turn is told its instructions and its state again
                self._context_seen[task_id] = (await measure()) or 0.0
                return
            if not opened.session.alive:
                break
        await self._fresh_start(task_id, "uncompacted")

    async def _flush(self, task_id: str, opened: OpenSession, bot: Bot) -> None:
        """Before compacting, or about 50 minutes into idleness while the cache is warm: the bot sets down where the
        task stands (`update_state`) and what's worth keeping from it in today's log. Nobody sees the turn; a quiet
        line in the thread says it happened."""
        done, _ = await self._hidden(task_id, opened.session.turn(prompts.text("flush", self.crew.home,
                                                                                 no_reply=NO_REPLY)), bot)
        text = (done.final_text if done and not done.is_error else "").strip()
        lines = [] if is_no_reply(text) else [ln.strip(" -*") for ln in text.splitlines() if ln.strip(" -*")]
        for line in lines[:FLUSH_LINES]:
            self.crew.memory.log(bot.name, f"{task_id} {line}")
        self._flushed.add(task_id)
        self._record(task_id, kind="flushed", data={"logged": min(len(lines), FLUSH_LINES)})

    async def _roll_over(self, task_id: str, opened: OpenSession, bot: Bot, percent: float) -> None:
        """For a session that can't compact on request: a briefing, written by its own bot, and the next turn starts
        a fresh session from it, instead of the CLI compacting it over and over."""
        done, _ = await self._hidden(task_id, opened.session.turn(prompts.text("briefing", self.crew.home)), bot)
        briefing = done.final_text if done and not done.is_error else ""
        if not briefing.strip():
            return  # no briefing, no rollover: the CLI's own compaction carries on
        await self._close_session(task_id)
        self._context_seen.pop(task_id, None)  # the fresh session starts from the briefing
        self.store.update_task(task_id, briefing=prompts.text("rolled-over", self.crew.home, briefing=briefing),
                               session_id=None)
        self._record(task_id, kind="rollover", data={"percent": round(percent, 1), "briefing": briefing})

    async def _without_stale_session(self, task: Task, bot: Bot) -> Task:
        """A session gone cold (`_gone_cold`), or made by another backend (the bot's was changed) can't be resumed
        sensibly: the task starts afresh."""
        mind = self._mind(task)
        if self._gone_cold(mind):
            await self._fresh_start(mind.id, "idle")
            return self.store.get_task(task.id)
        if not mind.session_id or mind.session_backend in (None, bot.backend):
            return task
        await self._forget_session(mind.id, f"its session was made by {mind.session_backend}, and "
                                            f"{bot.name} now runs on {bot.backend}")
        return self.store.get_task(task.id)

    async def _forget_session(self, task_id: str, why: str) -> None:
        """Drops a stored session id that can't be resumed: the next turn starts a fresh session, whose system prompt
        already carries the working state and the crew's updates."""
        await self._close_session(task_id)
        self._context_seen.pop(task_id, None)
        self.store.update_task(task_id, session_id=None, session_backend=None)
        self.store.set_primed(task_id, {})
        self._record(task_id, kind="notice", data={"message": f"Starting a fresh session: {why}."})

    async def _fresh_start(self, task_id: str, why: str) -> None:
        """The task carries on in a fresh session, which starts from its bot's notes, logs and the task's state, and
        the conversation's last few messages: when it sat idle past its cache (`idle`), or couldn't be compacted."""
        await self._close_session(task_id)
        self._context_seen.pop(task_id, None)
        said = [f"[{e.data.get('from', 'user') if e.kind == 'message' else self.store.get_task(task_id).bot}]: "
                f"{_short(e.data.get('text', ''), MESSAGE_CHARS)}"
                for e in self.store.last_events(task_id, ("message", "text"), FRESH_MESSAGES)]
        self.store.update_task(task_id, session_id=None, briefing=prompts.text(
            f"fresh-{why}", self.crew.home, messages="\n\n".join(said) or "(none)"))
        self._record(task_id, kind="fresh_session", data={"why": why})

    def _gone_cold(self, task: Task) -> bool:
        """Whether resuming the task's session would mean reading a big conversation back in after the prompt cache
        dropped it (an hour after its last turn): a fresh session from notes, logs and state is far cheaper then."""
        ended = self._turn_ended.get(task.id)
        return (bool(task.session_id) and task.kind in ("chat", "thread", "seat") and ended is not None
                and time.time() - ended >= self.cold_s and (self._context_seen.get(task.id) or 0) >= COLD_PERCENT)

    async def _flush_idle(self) -> None:
        """About 50 minutes after a big conversation's last turn, while the cache still has it, its bot sets down where
        things stand, so a fresh session after the cache has gone (`_gone_cold`) loses nothing."""
        now = time.time()
        for task_id, ended in list(self._turn_ended.items()):
            if (task_id in self._flushed or self._session_busy(task_id) or task_id in self._cancelling
                    or not self.flush_idle_s <= now - ended < self.cold_s
                    or (self._context_seen.get(task_id) or 0) < COLD_PERCENT):
                continue
            self._flushed.add(task_id)
            self._runs[task_id] = asyncio.create_task(self._idle_flush(task_id), name=f"flush {task_id}")

    async def _idle_flush(self, task_id: str) -> None:
        try:
            async with self._mind_lock(self.store.get_task(task_id)), self._turn_slot(task_id):
                task = self.store.get_task(task_id)
                bot = self.bot_for(task.bot)
                if bot and task.status is Status.IDLE and task.session_id:
                    backend = self.backends[bot.backend]
                    opened = await self._session_for(task, bot, self._cwd(task, bot), backend)
                    if opened:
                        await self._flush(task_id, opened, bot)
        except Exception as e:  # nothing was lost: the log and state just aren't fresher
            self._record(task_id, kind="notice", data={"message": f"couldn't set down where things stand: {e}"})
        finally:
            self._runs.pop(task_id, None)
            if waiting := self._waiting.pop(task_id, None):  # messages that came in meanwhile
                self._start(task_id, "\n\n".join(waiting))
            self._stay_awake()

    async def _hidden(self, task_id: str, stream: AsyncIterator[Event], bot: Bot) -> tuple[Done | None, list[Event]]:
        """A turn crewbots runs for itself (a flush, a compaction, a briefing): nothing of it reaches the thread, but
        what it costs counts like any turn's."""
        from crewbots import branch_mode

        events: list[Event] = []
        task = self.store.get_task(task_id)
        work = self.store.get_task(task.review_of) if task.review_of else task
        checkout = self._branch_checkout(work) if work.base_branch else None
        try:
            start = branch_mode.turn_head(checkout) if checkout else None
        except worktree.GitError as e:
            message = f"{work.repo}: {e}; the turn was stopped"
            self._set(task_id, status=Status.FAILED, error=message)
            self._branch_notice(task_id, message)
            await self._close_session(task_id)
            raise NotAllowed(message) from None
        changed = False
        current = asyncio.current_task()

        async def watch() -> None:
            nonlocal changed
            while True:
                await asyncio.sleep(2)
                if not branch_mode.turn_safe(checkout, start):
                    changed = True
                    current.cancel()
                    return

        watcher = asyncio.create_task(watch()) if checkout else None
        try:
            async with aclosing(stream) as turn:
                async for event in turn:
                    events.append(event)
        except asyncio.CancelledError:
            if not changed:
                raise
        except Exception as e:
            events.append(Done(final_text="", is_error=True, error=str(e)))
        finally:
            if watcher:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
                changed = changed or not branch_mode.turn_safe(checkout, start)
            if changed:
                message = f"{work.repo}: HEAD left {work.branch} or its history was rewritten; the turn was stopped"
                self._set(task_id, status=Status.FAILED, error=message)
                self._branch_notice(task_id, message)
                await self._close_session(task_id)
                raise NotAllowed(message)
        for usage in (e for e in events if isinstance(e, Usage)):
            self.store.append_event(task_id, kind="usage", data=_usage_data(usage, bot))
            self.store.add_usage(task_id, usage.input_tokens, usage.output_tokens, usage.cost_usd)
        for compaction in (e for e in events if isinstance(e, Compacted) and e.trigger == "auto"):
            self._record(task_id, compaction)  # the CLI compacted it on its own, as this turn began
            self.store.set_primed(task_id, {})
        return next((e for e in reversed(events) if isinstance(e, Done)), None), events

    async def _close_session(self, task_id: str) -> None:
        opened = self._sessions.pop(task_id, None)
        if opened:
            self._tokens.pop(opened.token, None)
            with contextlib.suppress(Exception):
                await opened.session.close()

    async def _make_room(self) -> None:
        idle = sorted((s.last_used, t) for t, s in self._sessions.items() if not self._session_busy(t))
        while len(self._sessions) >= self.max_sessions and idle:
            await self._close_session(idle.pop(0)[1])

    async def _reap_idle(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(max(0.05, min(60.0, self.idle_timeout_s / 4)))
            now = loop.time()
            for task_id, opened in list(self._sessions.items()):
                if not self._session_busy(task_id) and now - opened.last_used >= self.idle_timeout_s:
                    await self._close_session(task_id)
            await self._flush_idle()


def _own(row: Any, bot: str) -> str:
    """What a line of a bot's Elsewhere is, as its own: its chat, a group chat it's in, or its own work and who for."""
    if row["kind"] == "chat":
        return f"your chat with the user ({row['id']})"
    if row["kind"] == "seat":
        return f"your group chat {' '.join(row['room'].split())[:40]!r} ({row['room_id']})"
    asker = row["created_by"]
    whom = "" if asker in (bot, "crewbots") else " for the user" if asker == "user" else f" for {asker}"
    where = f" in {' '.join(row['asked_in'].split())[:40]!r}" if row["asked_in"] else ""
    return f"your {row['kind']} {row['id']}{whom}{where}"


def _short(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[:chars - 1] + "…"
