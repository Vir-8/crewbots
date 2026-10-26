"""Long-lived sessions: one per active task, opened when needed, reused across turns, closed when idle, and rolled over
into a fresh one from a briefing when the conversation grows long."""

import asyncio
import contextlib
import secrets
from contextlib import aclosing
from pathlib import Path
from typing import Any

from crewbots import prompts
from crewbots.backends import Backend
from crewbots.crew import Bot
from crewbots.events import Done, Usage
from crewbots.runner.common import OpenSession, _plan, _usage_data
from crewbots.store import Task


class Sessions:
    """The part of Runner that keeps sessions open, reuses them, and rolls a long one over."""

    def open_sessions(self) -> list[str]:
        return list(self._sessions)

    async def status(self, task_id: str) -> dict[str, Any]:
        """What `/status` shows for a chat or thread: its bot's model and effort, its session, and how full the
        session's context is: measured now if the session is open and idle, else as last measured."""
        task = self.store.get_task(task_id)
        bot = self.bot_for(task.bot)
        opened = self._sessions.get(task_id)
        percent = self._context_seen.get(task_id)
        measure = getattr(opened.session, "context_percent", None) if opened and opened.session.alive else None
        if measure and task_id not in self._runs:
            with contextlib.suppress(Exception):
                percent = await asyncio.wait_for(measure(), 10) or percent
                if percent is not None:
                    self._context_seen[task_id] = percent
        return {
            "task_id": task.id, "bot": task.bot, "backend": bot.backend if bot else None,
            "plan": _plan(bot.backend) if bot else None, "model": bot.model if bot else None,
            "effort": bot.effort if bot else None, "session_id": task.session_id, "open": opened is not None,
            "context_percent": round(percent, 1) if percent is not None else None,
            "context_window": bot.context_window if bot and task.kind != "review" else None,
            "rollover_percent": self.rollover_percent, "turns": task.turns,
        }

    async def _session_for(self, task: Task, bot: Bot, cwd: Path, backend: Backend) -> OpenSession | None:
        """The task's open session, or a new one; None to run this turn as its own process."""
        current = self._sessions.get(task.id)
        if current and current.session.alive and current.cwd == cwd:
            return current
        if current:
            await self._close_session(task.id)
        if not self.use_sessions or not hasattr(backend, "open"):
            return None
        await self._make_room()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = task.id  # its MCP server may call in while the session starts
        try:
            session = await backend.open(self._spec(task, bot, cwd, token, "", backend))
        except Exception as e:
            self._tokens.pop(token, None)
            self._record(task.id, kind="notice", data={"message": f"couldn't keep a session open ({e}); "
                                                                  "running this turn on its own"})
            return None
        opened = OpenSession(session=session, token=token, cwd=cwd, last_used=asyncio.get_running_loop().time())
        self._sessions[task.id] = opened
        return opened

    async def _maybe_roll_over(self, task_id: str, opened: OpenSession) -> None:
        """A conversation past `rollover_percent` of its context gets a briefing, written by its own bot, and the next
        turn starts a fresh session from it, instead of the CLI compacting it over and over."""
        measure = getattr(opened.session, "context_percent", None)
        if not self.rollover_percent or measure is None:
            return
        percent = await measure()
        if percent is not None:
            self._context_seen[task_id] = percent
        if percent is None or percent < self.rollover_percent:
            return
        briefing, usage = "", None
        try:
            async with aclosing(opened.session.turn(prompts.text("briefing", self.crew.home))) as stream:
                async for event in stream:
                    if isinstance(event, Usage):
                        usage = event
                    elif isinstance(event, Done) and not event.is_error:
                        briefing = event.final_text
        except Exception:
            briefing = ""
        if usage:  # it counts toward budgets like any turn
            bot = self.bot_for(self.store.get_task(task_id).bot)
            if bot:
                self.store.append_event(task_id, kind="usage", data=_usage_data(usage, bot))
            else:
                self.store.append_event(task_id, usage)
            self.store.add_usage(task_id, usage.input_tokens, usage.output_tokens, usage.cost_usd)
        if not briefing.strip():
            return  # no briefing, no rollover: the CLI's own compaction carries on
        await self._close_session(task_id)
        self._context_seen.pop(task_id, None)  # the fresh session starts from the briefing
        self.store.update_task(task_id, briefing=briefing, session_id=None)
        self._record(task_id, kind="rollover", data={"percent": round(percent, 1), "briefing": briefing})

    async def _close_session(self, task_id: str) -> None:
        opened = self._sessions.pop(task_id, None)
        if opened:
            self._tokens.pop(opened.token, None)
            with contextlib.suppress(Exception):
                await opened.session.close()

    async def _make_room(self) -> None:
        idle = sorted((s.last_used, t) for t, s in self._sessions.items() if t not in self._runs)
        while len(self._sessions) >= self.max_sessions and idle:
            await self._close_session(idle.pop(0)[1])

    async def _reap_idle(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(max(1.0, min(60.0, self.idle_timeout_s / 4)))
            now = loop.time()
            for task_id, opened in list(self._sessions.items()):
                if task_id not in self._runs and now - opened.last_used >= self.idle_timeout_s:
                    await self._close_session(task_id)
