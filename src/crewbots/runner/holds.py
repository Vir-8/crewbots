"""When a turn may start: budgets and plan limits (a task waits, held, until there's room), pausing, and restarting once
the crew is idle."""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from crewbots.events import RateLimit
from crewbots.runner.common import _iso, _plan
from crewbots.store import Status, Task

BUDGET_REPORT_WINDOW = timedelta(days=7)  # what `budgets` reports for a bot without a budget
LIMIT_WINDOWS = {"five_hour": "five-hour", "seven_day": "weekly", "seven_day_opus": "weekly Opus",
                 "seven_day_sonnet": "weekly Sonnet", "5h": "five-hour", "7d": "weekly"}
HOLD_RECHECK_S = 60.0  # a held task looks again this often: budgets free up as old usage ages out of their window


class Holds:
    """The part of Runner that decides when a turn may start: budgets, plan limits, pausing and restarting."""

    def held(self) -> list[str]:
        return list(self._held)

    @property
    def paused(self) -> bool:
        return self._user_paused if self.restarting else not self._resumed.is_set()

    def pause(self) -> None:
        self._user_paused = True
        if not self.restarting:
            self._resumed.clear()
        self.bus.publish({"type": "crew", "crew": self.state()})

    def resume(self) -> None:
        self._user_paused = False
        if not self.restarting:
            self._resumed.set()
        self.bus.publish({"type": "crew", "crew": self.state()})

    def restart_when_idle(self) -> None:
        if not self.restarting:
            self._user_paused = self.paused
            self.restarting = True
            self._resumed.clear()
            self.bus.publish({"type": "crew", "crew": self.state()})

    def cancel_restart(self) -> None:
        if self.restarting:
            self.restarting = False
            if not self._user_paused:
                self._resumed.set()
            self.bus.publish({"type": "crew", "crew": self.state()})

    def turns_running(self) -> int:
        """Turns under way now: not ones waiting their turn, held, or waiting to try again, which a restart carries
        on as they were."""
        return sum(1 for t in list(self._runs) if self.store.get_task(t).status == Status.RUNNING)

    async def idle(self, timeout_s: float, poll_s: float = 1.0) -> bool:
        """Waits until no turn is running, for at most `timeout_s`. Whether it got there."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while self.turns_running():
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(poll_s)
        return True

    def release(self, task_id: str) -> Task:
        """The user lets a held task, and the rest of its goal, go past its budgets."""
        task = self.store.get_task(task_id)
        root = self.store.work_of(task_id)
        self._released.add(root.id)
        self._record(task_id, kind="released", data={"goal": root.id})
        self._wake_held()
        return task

    def budgets(self) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        rows = []
        for bot in self.crew.bots.values():
            window = bot.budget.window if bot.budget else BUDGET_REPORT_WINDOW
            rows.append({"bot": bot.name, "tokens": bot.budget.tokens if bot.budget else None,
                         "per": bot.budget.per if bot.budget else "week",
                         "used": self.store.bot_usage(bot.name, _iso(now - window)),
                         "held": sorted(t for t in self._held if self.store.get_task(t).bot == bot.name)})
        return rows

    def hold_reason(self, task: Task) -> str | None:
        """Why the task's next turn has to wait, or None if it can go ahead."""
        root = self.store.work_of(task.id)
        bot = self.bot_for(task.bot)
        if root.id in self._released or bot is None:
            return None
        return self.budget_reason(bot.name, task)

    def budget_reason(self, name: str, within: Task) -> str | None:
        """Why bot `name` can't take a turn now in the work `within` belongs to: its budget, the work's, or its plan's
        limit. None if it can."""
        root = self.store.work_of(within.id)
        bot = self.bot_for(name)
        if root.id in self._released or bot is None:
            return None
        if bot.budget:
            used = self.store.bot_usage(bot.name, _iso(datetime.now(UTC) - bot.budget.window))
            if used >= bot.budget.tokens:
                return (f"{bot.name} has used {used:,} of its {bot.budget.tokens:,} tokens for the last {bot.budget.per}. "
                        "It goes on by itself as older use leaves that window, or when you release it.")
        if root.budget:
            used = self.store.tree_usage(root.id)
            if used >= root.budget:
                return (f"this run of the routine {root.routine!r} has used {used:,} of its {root.budget:,} tokens. "
                        "Release it to let the run finish.")
        plan = _plan(bot.backend)
        for limit in self.limits.get(plan, {}).values():
            if limit.status == "limited" and limit.resets_at and limit.resets_at > time.time():
                resets = datetime.fromtimestamp(limit.resets_at).strftime("%a %H:%M")
                window = LIMIT_WINDOWS.get(limit.window, limit.window.replace("_", " "))
                return f"the {plan.title()} plan's {window} limit is reached; it resets {resets}."
        return None

    async def _within_budget(self, task_id: str) -> None:
        held = False
        try:
            while reason := self.hold_reason(self.store.get_task(task_id)):
                if not held:
                    held = True
                    self._held.add(task_id)
                    self._stay_awake()  # a held task isn't work in progress
                    self._set(task_id, status=Status.HELD)
                    self._record(task_id, kind="held", data={"reason": reason})
                    self.bus.publish({"type": "crew", "crew": self.state()})
                recheck = self._recheck
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(recheck.wait(), HOLD_RECHECK_S)
                await self._resumed.wait()
        finally:
            if held:
                self._held.discard(task_id)
                self._stay_awake()
                self.bus.publish({"type": "crew", "crew": self.state()})

    def _wake_held(self) -> None:
        self._recheck.set()

    @contextlib.asynccontextmanager
    async def _turn_slot(self, task_id: str) -> AsyncIterator[None]:
        """A run slot, once the crew isn't paused and the task is within its budgets. A pause (or a restart) that came
        while it waited for the slot sends it back to wait, so no turn starts after one."""
        while True:
            await self._resumed.wait()
            await self._within_budget(task_id)
            await self._slots.acquire()
            if self._resumed.is_set():
                break
            self._slots.release()
        try:
            yield
        finally:
            self._slots.release()
        self._recheck = asyncio.Event()

    def _note_limit(self, task_id: str, limit: RateLimit) -> None:
        """Keeps the plan's latest limit, and tells the crew when its status changes (getting close, reached)."""
        windows = self.limits.setdefault(limit.plan, {})
        previous = windows.get(limit.window)
        windows[limit.window] = limit
        if (previous.status if previous else "ok") != limit.status:
            self._record(task_id, kind="rate_limit", data={k: v for k, v in asdict(limit).items() if k != "kind"})
            self.bus.publish({"type": "crew", "crew": self.state()})
