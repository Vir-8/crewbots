"""Retry failed turns and expose their current attempt state."""

import asyncio

from crewbots import prompts
from crewbots.events import Notice
from crewbots.runner.common import _duration
from crewbots.store import Status


class Retries:
    async def _tries(self, task_id: str, prompt: str, owed: bool = False, nudged: bool = False) -> None:
        try:
            await self._tries_inner(task_id, prompt, owed, nudged)
        finally:
            self._attempts.pop(task_id, None)

    async def _tries_inner(self, task_id: str, prompt: str, owed: bool = False, nudged: bool = False) -> None:
        """Runs a turn, and again when it failed for a passing reason: after a wait, or for a plan's limit, once the
        limit resets (the hold sees to that). A turn that had started a session picks up where it stopped."""
        for attempt in range(len(self.retry_waits_s) + 1):
            self._attempts[task_id] = {"attempt": attempt + 1, "of": len(self.retry_waits_s) + 1,
                                       "state": "starting"}
            done = await self._run(task_id, prompt, owed, nudged)
            if done is None:
                return  # settled: finished, or failed for good
            task = self.store.get_task(task_id)
            if not done.retry or attempt == len(self.retry_waits_s):
                self._set(task_id, status=Status.FAILED, result=done.final_text or None, error=done.error)
                return
            held = done.retry == "limit" and self.hold_reason(task)
            wait = 0.0 if held else self.retry_waits_s[attempt]
            then = "when the limit resets" if held else f"in {_duration(wait)}"
            # Shown right under the failed turn's own "Stopped: <error>".
            self._record(task_id, Notice(message=f"Trying again {then} (try {attempt + 2} of "
                                                 f"{len(self.retry_waits_s) + 1})."))
            self._set(task_id, status=Status.QUEUED)
            self._attempts[task_id] = {"attempt": attempt + 2, "of": len(self.retry_waits_s) + 1,
                                       "state": "backoff", "wait_seconds": wait}
            await asyncio.sleep(wait)
            if task.session_id:
                prompt = prompts.text("retry", self.crew.home, error=done.error)

