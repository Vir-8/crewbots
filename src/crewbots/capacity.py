"""Spare capacity: whether the crew's background work (memory consolidation, check-ins, work a bot starts on its own)
may run now, decided in one place.

It runs only with headroom: never while the user's own work is running, never once a plan's limit is close (as the
CLI reports it, or under MIN_LEFT percent left in any of its windows), so it never pushes the user into a limit or
delays their turn. It runs at full strength (model and effort are never lowered), and a window about to reset with
plenty unused is worth using now: the reason says so. `/status` shows the reason.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from crewbots.runner import Runner

MIN_LEFT = 20.0  # percent: below this left in any window of a plan, its background work waits
SOON_S = 2 * 3600  # a window resetting within this, with
UNUSED = 40.0  # at least this percent left, is capacity about to go to waste


@dataclass(frozen=True)
class Verdict:
    go: bool
    reason: str  # "running, 64% left in the 5 hours window", or why it waits
    left: float | None = None  # the least left in any window of the plan, if known


class Capacity:
    """The crew's one decider for background work, from the plans' limits."""

    def __init__(self, runner: "Runner"):
        self.runner = runner
        self.source: Callable[[], Awaitable[dict[str, Any]]] | None = None  # the plans' limits: the daemon's `plans`
        self.plans: dict[str, Any] = {}

    async def refresh(self) -> None:
        """Takes up the plans' latest limits (kept answers come back at once; stale ones refresh behind them)."""
        if self.source:
            self.plans = await self.source()

    def verdict(self, plan: str) -> Verdict:
        if plan == "antigravity":
            return Verdict(False, "Antigravity runs requested work only")
        runner = self.runner
        if runner.paused:
            return Verdict(False, "waiting while the crew is paused")
        if runner.user_busy():
            return Verdict(False, "waiting while your own work runs")
        for limit in runner.limits.get(plan, {}).values():  # what the CLI said on its last turn
            if limit.status in ("warning", "limited"):
                close = "reached" if limit.status == "limited" else "close"
                return Verdict(False, f"waiting: the {plan.title()} plan's {limit.window.replace('_', ' ')} limit is {close}")
        windows = [w for w in (self.plans.get(plan) or {}).get("windows") or [] if w.get("used_percent") is not None]
        if not windows:
            return Verdict(True, "running (its plan's limits aren't known yet)")
        tightest = min(windows, key=lambda w: 100 - w["used_percent"])
        left = 100 - tightest["used_percent"]
        where = f"{left:.0f}% left in the {tightest['name'].lower()} window"
        if left < MIN_LEFT:
            return Verdict(False, f"waiting: {where}, under {MIN_LEFT:.0f}%", left)
        soon = [w for w in windows if w.get("resets_at") and 0 < w["resets_at"] - time.time() <= SOON_S
                and 100 - w["used_percent"] >= UNUSED]
        if soon:
            where += f"; the {soon[0]['name'].lower()} window resets soon with {100 - soon[0]['used_percent']:.0f}% unused"
        return Verdict(True, f"running, {where}", left)

    def summary(self) -> list[dict[str, Any]]:
        """Each plan's verdict, for the crew's state."""
        return [{"plan": plan, "go": v.go, "reason": v.reason} for plan in ("claude", "codex")
                if (v := self.verdict(plan))]
