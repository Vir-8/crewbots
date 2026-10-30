"""Consolidating memory overnight ("dreaming"): each bot, with its own model at full strength, reads what it logged and
noted since the last time, and crewbots applies what it decides to its notes and its CORE.md. Weekly, it also merges
duplicate notes. The memory folder is committed after each run, so every change can be looked back on and undone."""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

from crewbots import prompts
from crewbots.backends import Access, RunSpec
from crewbots.crew import Bot
from crewbots.memory import CREW_SCOPE, MemoryError, Note
from crewbots.runner.common import UnknownBot, plan
from crewbots.store import Status, Task

DREAM_HOUR = 3  # local time: a night's consolidation is due from then
DREAM_UNTIL = 8  # and runs before this, while the crew is idle; a Mac asleep then has it after an hour of quiet
QUIET_S = 3600.0
WEEKLY_DAY = 6  # Sunday (Monday is 0): the night's pass also merges duplicate notes
MATERIAL_CHARS = 60_000  # of logs and notes in one pass; the newest are kept


class Dreams:
    """The part of Runner that consolidates bots' memory."""

    def dream_due(self, bot: str, now: datetime) -> bool:
        """Whether `bot`'s memory is due: once a night (noon to noon), with something new since, in the early
        hours or after an hour of quiet, when there's spare capacity (`capacity.py`: never beside the user's work)."""
        if self._dreaming:
            return False  # one bot at a time
        quiet = time.time() - max(self._turn_ended.values(), default=self._started) >= QUIET_S
        if not (DREAM_HOUR <= now.hour < DREAM_UNTIL or quiet):
            return False
        last = (self.store.routine_state(f"memory:{bot}") or {}).get("last_run_at")
        # A night runs noon to noon, so one consolidated in a quiet evening isn't consolidated again in the early hours.
        night = lambda t: (t - timedelta(hours=12)).date()  # noqa: E731
        if last and night(datetime.fromisoformat(last)) >= night(now):
            return False
        since = (last or "0000-00-00")[:10]
        memory = self.crew.memory
        if not (memory.logs_since(bot, since) or memory.changed_since(bot, since)) or bot not in self.crew.bots:
            return False
        return self.capacity.verdict(plan(self.crew.bots[bot].backend)).go

    def dream(self, bot: str, weekly: bool | None = None) -> Task:
        """Starts consolidating one bot's memory. The run is a task of its own, so what it cost is counted, and its
        result says what changed."""
        if bot not in self.crew.bots:
            raise UnknownBot(f"no bot named {bot!r}; the crew is {sorted(self.crew.bots)}")
        now = datetime.now().astimezone()
        task = self.store.create_task(bot, f"Consolidate {self.crew.bots[bot].label}'s memory ({now:%a %d %b})",
                                      created_by="crewbots", status=Status.QUEUED)
        self.bus.publish({"type": "task", "task": task.to_json()})
        self._dreaming.add(bot)
        self._runs[task.id] = asyncio.create_task(self._dream(task, now, weekly), name=f"dream {bot}")
        return task

    async def _dream(self, task: Task, now: datetime, weekly: bool | None) -> Task:
        bot, b = task.bot, self.crew.bots[task.bot]
        weekly = now.weekday() == WEEKLY_DAY if weekly is None else weekly
        last = (self.store.routine_state(f"memory:{bot}") or {}).get("last_run_at") or ""
        since = last[:10] or (now.date() - timedelta(days=1)).isoformat()
        try:
            async with self._turn_slot(task.id):
                self._set(task.id, status=Status.RUNNING)
                reply, error = await self._dream_call(task, b, self._materials(b, since, weekly), weekly)
            if error:
                return self._set(task.id, status=Status.FAILED, error=error)
            summary = self._apply_dream(bot, reply)
            commit = self.crew.memory.commit(f"Consolidated {bot}'s memory, {now:%Y-%m-%d}{' (weekly)' if weekly else ''}")
            self._record(task.id, kind="consolidated", data={**summary, "commit": commit, "weekly": weekly})
            self.store.routine_ran(f"memory:{bot}", now.isoformat(timespec="seconds"), task.id)
            return self._set(task.id, status=Status.IDLE, result=summary["text"])
        except Exception as e:  # a reply that couldn't be read or applied: nothing half-done is committed
            return self._set(task.id, status=Status.FAILED, error=f"crewbots: {e}")
        finally:
            self._dreaming.discard(bot)
            self._runs.pop(task.id, None)

    def _materials(self, bot: Bot, since: str, weekly: bool) -> str:
        memory = self.crew.memory
        notes = memory.notes(bot.name)
        index = lambda ns: "\n".join(f"- {n.name}: {n.title} — {n.gist}" for n in ns) or "(none)"  # noqa: E731
        full = notes if weekly else memory.changed_since(bot.name, since)
        others = "\n".join(f"- {other}/{n.name}: {n.title} — {n.gist}" for other in self.crew.bots if other != bot.name
                           for n in memory.notes(other)) or "(none)"
        logs = memory.logs_since(bot.name, since)
        return prompts.text(
            "dream", self.crew.home, bot=f"{bot.label}, the {bot.title}", core=memory.core(bot.name) or "(none yet)",
            index=index(notes), since=since, notes=_newest(full, MATERIAL_CHARS // 2),
            logs=logs[-MATERIAL_CHARS // 2:] or "(none)", crew=index(memory.notes(CREW_SCOPE)), others=others,
            weekly=prompts.text("dream-weekly", self.crew.home) if weekly else "")

    async def _dream_call(self, task: Task, bot: Bot, materials: str, weekly: bool) -> tuple[dict[str, Any], str | None]:
        spec = RunSpec(prompt=materials, cwd=self.crew.home, access=Access.READ_ONLY, model=bot.model,
                       system_prompt=prompts.text("dream-setting", self.crew.home), effort=bot.effort,
                       timeout_s=bot.timeout_s, network=False, deny_read=self._state_denials(bot.name))
        try:
            reply = await self._json_run(task, spec, "the consolidation run")
        except ValueError as e:
            return {}, f"the consolidation's reply wasn't JSON: {e}"
        except RuntimeError as e:
            return {}, str(e)
        if reply is None:
            return {}, "the consolidation's reply had no JSON in it"
        return reply, None

    def _apply_dream(self, bot: str, reply: dict[str, Any]) -> dict[str, Any]:
        """Applies the consolidation's decisions to the bot's own notes (and the crew's, for what it shares or
        promotes), and its CORE.md. A decision about a note that isn't there is skipped, not fatal."""
        memory, source = self.crew.memory, f"consolidation {datetime.now().astimezone():%Y-%m-%d}"
        done: dict[str, int] = {}
        skipped = []
        for op in reply.get("notes") or []:
            kind, name = op.get("op"), str(op.get("name") or "")
            try:
                match kind:
                    case "add":
                        memory.remember(CREW_SCOPE if op.get("shared") else bot, op["title"], op["body"], source)
                    case "update":
                        note = next(n for n in memory.notes(bot) if n.name == name)
                        memory.remember(bot, note.title, op["body"], source)
                    case "supersede":
                        new = memory.remember(bot, op["title"], op["body"], source)
                        memory.supersede(bot, name, new.name)
                    case "delete":
                        memory.forget(bot, name)
                    case "promote":
                        memory.promote(bot, name)
                    case _:
                        raise MemoryError(f"no such operation {kind!r}")
                done[kind] = done.get(kind, 0) + 1
            except (KeyError, StopIteration, MemoryError) as e:
                skipped.append(f"{kind} {name}: {e}")
        if core := (reply.get("core") or "").strip():
            memory.write_core(bot, core)
        counts = ", ".join(f"{n} {kind}" for kind, n in done.items()) or "no note changed"
        text = f"{reply.get('summary') or 'Consolidated.'} ({counts}{', CORE.md rewritten' if core else ''})"
        return {"text": text, "counts": done, "core": bool(core), "skipped": skipped}


def _newest(notes: list[Note], budget: int) -> str:
    """As many whole notes as fit in `budget` characters, from the newest (the first); only a newest note longer than
    that is cut."""
    parts: list[str] = []
    used = 0
    for n in notes:
        part = f"## {n.name}\n\n{n.body}"
        if parts and used + len(part) > budget:
            break
        parts.append(part[:budget])
        used += len(part) + 2
    return "\n\n".join(parts) or "(none)"
