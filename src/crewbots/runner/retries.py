"""Retry failed turns and expose their current attempt state."""

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Literal

from crewbots import prompts
from crewbots.events import Notice
from crewbots.runner.common import _duration
from crewbots.store import Status, Task

# What a backend says when the session it was asked to resume is gone: Codex's app-server, and Claude.
SESSION_GONE = re.compile(r"no rollout found|no conversation found", re.IGNORECASE)


@dataclass
class Attempt:
    """Where a task's current try at a turn stands, while `_tries` runs it."""
    attempt: int
    of: int
    state: Literal["starting", "running", "stalled", "backoff"] = "starting"
    wait_seconds: float = 0.0  # before the next try, in backoff
    last_activity: float | None = None  # time.monotonic() of the backend's last event


class Retries:
    def retrying(self, task_id: str) -> dict[str, int | float] | None:
        """Where a task waiting to try again stands (from the same state `room_status` reads); None otherwise."""
        a = self._attempts.get(task_id)
        if not a or a.state != "backoff":
            return None
        return {"attempt": a.attempt, "of": a.of, "wait_seconds": a.wait_seconds}

    def task_json(self, task: Task) -> dict[str, Any]:
        """A task as the API and the app's live updates show it: with its try, while it waits to try again."""
        return {**task.to_json(), "attempt": self.retrying(task.id)}

    def _failed(self, task_id: str, error: str | None, result: str | None = None, retry: str | None = None) -> None:
        """A turn failed for good, whether it ran or never started. The task settles as failed; a seat's room is told
        once, as the bot, with a fixed summary of the kind of failure. The error itself stays where `room_status`
        reads it."""
        self._set(task_id, status=Status.FAILED, result=result, error=error)
        task = self.store.get_task(task_id)
        if task.kind != "seat" or not task.room_id:
            return
        text = (error or "").lower()
        kind = ("rate limited" if retry == "limit" else "session couldn't be resumed" if SESSION_GONE.search(text)
                else "workspace missing" if "workspace" in text and "does not exist" in text
                else "bot not found" if "no bot named" in text else "backend error" if retry else "unexpected error")
        try:
            self.post(task.room_id, task.bot, f"I couldn't take this turn: {kind}. Details: room_status.",
                      depth=self._seat_depth.get(task_id, 0) + 1)
        except KeyError:  # its room was deleted meanwhile
            pass

    async def _tries(self, task_id: str, prompt: str, owed: bool = False, nudged: bool = False) -> None:
        """Runs a turn, and again when it failed for a passing reason: after a wait, or for a plan's limit, once the
        limit resets (the hold sees to that). A turn that had started a session picks up where it stopped."""
        try:
            if (task := self.store.get_task(task_id)).review_of and (prompt := await self._checks_first(task, prompt)) is None:
                return  # a check its work was handed back with failed: the round is settled without a reviewer's turn
            original, fresh, attempt = prompt, False, 0
            while attempt < len(self.retry_waits_s) + 1:
                self._attempts[task_id] = Attempt(attempt + 1, len(self.retry_waits_s) + 1)
                done = await self._run(task_id, prompt, owed, nudged)
                if done is None:
                    return  # settled: finished, or failed for good
                task = self.store.get_task(task_id)
                bot = self.bot_for(task.bot)
                if bot and not self.automatic(bot):
                    self._failed(task_id, done.error, done.final_text or None, done.retry)
                    return
                if task.session_id and not fresh and SESSION_GONE.search(done.error or ""):
                    # Waiting won't bring it back: once, at once, a fresh session takes the message.
                    fresh, prompt = True, original
                    await self._forget_session(task_id, "the session it was to resume no longer exists")
                    continue
                if not done.retry or attempt == len(self.retry_waits_s):
                    self._failed(task_id, done.error, done.final_text or None, done.retry)
                    return
                held = done.retry == "limit" and self.hold_reason(task)
                wait = 0.0 if held else self.retry_waits_s[attempt]
                then = "when the limit resets" if held else f"in {_duration(wait)}"
                # Shown right under the failed turn's own "Stopped: <error>".
                self._record(task_id, Notice(message=f"Trying again {then} (try {attempt + 2} of "
                                                     f"{len(self.retry_waits_s) + 1})."))
                self._attempts[task_id] = Attempt(attempt + 2, len(self.retry_waits_s) + 1, "backoff", wait)
                self._set(task_id, status=Status.QUEUED)
                await asyncio.sleep(wait)
                if task.session_id:
                    prompt = prompts.text("retry", self.crew.home, error=done.error)
                attempt += 1
        finally:
            self._attempts.pop(task_id, None)
