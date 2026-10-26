"""Checking in: a bot looks at what's due on its checklist (`checkins.py`), in a small run of its own with light
context, and says something in its chat only when there's something worth the user's attention."""

import asyncio
import json
import re
from datetime import datetime
from typing import Any

from crewbots import prompts
from crewbots.backends import Access, RunSpec
from crewbots.checkins import Item
from crewbots.events import Done, Usage
from crewbots.runner.common import UnknownBot, _usage_data
from crewbots.store import Status, Task

URGENCIES = ("low", "normal", "high")


class Checkins:
    """The part of Runner that runs bots' check-ins."""

    def bot_busy(self, bot: str) -> bool:
        """Whether any of the bot's work is running or waiting to."""
        return any(self.store.get_task(task_id).bot == bot for task_id in self._runs)

    def check_in(self, bot: str, items: list[Item], changes: str) -> Task:
        """Starts a check-in of `items` for `bot`. Its runs share one task of the bot's, so what they cost is counted
        without a thread for each."""
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        task = self._checkin_task(bot)
        self._set(task.id, status=Status.QUEUED)
        self._runs[task.id] = asyncio.create_task(self._check_in(task, items, changes), name=f"check-in {bot}")
        return task

    def _checkin_task(self, bot: str) -> Task:
        kept = (self.store.routine_state(f"checkin:{bot}") or {}).get("last_task_id")
        if kept:
            try:
                return self.store.get_task(kept)
            except KeyError:
                pass
        task = self.store.create_task(bot, f"{self.crew.bots[bot].label}'s check-ins", created_by="crewbots",
                                      status=Status.IDLE)
        self.store.routine_ran(f"checkin:{bot}", "", task.id)
        return task

    async def _check_in(self, task: Task, items: list[Item], changes: str) -> None:
        bot = self.crew.bots[task.bot]
        now = datetime.now().astimezone()
        try:
            async with self._turn_slot(task.id):
                self._set(task.id, status=Status.RUNNING)
                memory = self.crew.memory
                spec = RunSpec(
                    prompt=prompts.text("checkin", self.crew.home, items="\n".join(f"- {i.what}" for i in items),
                                        core=memory.core(task.bot) or "(none)", notes=memory.prompt(task.bot) or "(none)",
                                        changes=changes or "Nothing new."),
                    cwd=bot.workspace or self.crew.home, access=Access.READ_ONLY, model=bot.model, effort=bot.effort,
                    system_prompt=prompts.text("checkin-setting", self.crew.home, bot=f"{bot.label}, the {bot.title}",
                                               role=bot.description),
                    mcp_servers=dict(bot.mcp), timeout_s=bot.timeout_s, network=bot.network)
                report = await self._checkin_run(task, spec)
            for item in items:
                self.store.routine_ran(f"checkin:{task.bot}:{item.id}", now.isoformat(timespec="seconds"), task.id)
            self.store.routine_ran(f"checkin:{task.bot}", now.isoformat(timespec="seconds"), task.id)
            self._record(task.id, kind="checkin", data=report | {"items": [i.what for i in items]})
            if report["notify"] and report["text"]:
                chat = self.chat(task.bot)
                self._record(chat.id, kind="text", data={"text": report["text"], "checkin": True,
                                                          "urgency": report["urgency"]})
            self._set(task.id, status=Status.IDLE, result=report["text"] if report["notify"] else "Nothing to report.")
        except Exception as e:  # nothing reached the user; the items stay due, for the next tick
            self._set(task.id, status=Status.FAILED, error=f"crewbots: {e}")
        finally:
            self._runs.pop(task.id, None)

    async def _checkin_run(self, task: Task, spec: RunSpec) -> dict[str, Any]:
        bot = self.crew.bots[task.bot]
        done: Done | None = None
        async for event in self.backends[bot.backend].run(spec):
            if isinstance(event, Usage):
                self._record(task.id, kind="usage", data=_usage_data(event, bot))
                self.store.add_usage(task.id, event.input_tokens, event.output_tokens, event.cost_usd)
            elif isinstance(event, Done):
                done = event
        if done is None or done.is_error:
            raise RuntimeError((done.error if done else None) or "the check-in ended without a result")
        found = re.search(r"\{.*\}", done.final_text, re.DOTALL)
        reply = json.loads(found[0]) if found else {}
        start = reply.get("start_thread")
        return {"notify": bool(reply.get("notify")), "text": str(reply.get("text") or "").strip(),
                "urgency": reply.get("urgency") if reply.get("urgency") in URGENCIES else "normal",
                "start_thread": start if isinstance(start, dict) and start.get("goal") else None}
