"""Getting a crew started: the chief's first word in its chat, and bringing in what Claude Code and Codex already know
(the folders the user works in, as projects, and their memories). Both happen once, until the crew's been started off.

The greeting is the chief's own messages in its chat, kept like any others, with an offer under them that remembers
how the user answered it; what it said is in its session's briefing, so it carries on from there. What the chief
learns from the memories it learns in a thread of its own, so the chat stays a conversation; the thread's result
comes back to the chat, as a thread's does. A bot takes the name the user gives it with `set_name`.
"""

import dataclasses
from pathlib import Path
from typing import Any

from crewbots import cards, discovery, prompts
from crewbots import crew as crew_module
from crewbots import projects as projects_module
from crewbots.crew import Bot, CrewError
from crewbots.projects import Project
from crewbots.runner.common import NotAllowed
from crewbots.store import Task
from crewbots.store.records import _now

STARTED = "started"  # in the routines table: when the crew was started off (once), and the thread it learned in
NAME_CHARS = 40  # of the name a bot goes by


class Welcome:
    """The part of Runner that starts a crew off."""

    def coordinator(self) -> Bot | None:
        return self.crew.bots.get(self.store.chief())

    def is_chief(self, name: str) -> bool:
        chief = self.coordinator()
        return chief is not None and chief.name == name

    def is_coordinator(self, name: str) -> bool:
        return name in self.crew.bots and (self.is_chief(name) or name in (self.store.coordinators() or []))

    async def _refresh_authority_sessions(self) -> None:
        for task_id, opened in list(self._sessions.items()):
            task = self.store.get_task(task_id)
            if not self._session_busy(task_id) and (opened.coordinator != self.is_coordinator(task.bot)
                                               or opened.chief != self.is_chief(task.bot)):
                await self._close_session(task_id)

    async def set_coordinator(self, name: str) -> None:
        """Refresh idle sessions on a chief change; active turns finish before their sessions close."""
        if self.store.chief() != name:
            self.store.set_chief(name)
            await self._refresh_authority_sessions()

    async def set_coordinators(self, names: list[str]) -> None:
        if self.store.coordinators() != names:
            self.store.set_coordinators(names)
            await self._refresh_authority_sessions()

    def started(self) -> bool:
        return bool(self.crew.projects) or self.store.routine_state(STARTED) is not None

    def greet(self) -> Task | None:
        """The chief's first word in its chat, once: who it is, that it keeps things moving, and an offer to bring
        in what Claude Code and Codex know. Only at a first meeting: a crew the user has talked with counts as started
        off already."""
        chief = self.coordinator()
        if chief is None or self.started():
            return None
        if self.store.talked_with_crew():
            self.store.routine_ran(STARTED, _now(), "")
            return None
        chat = self.chat(chief.name)
        if self.store.last_events(chat.id, ("onboarding",), 1):
            return None
        named = bool(chief.display_name)
        said = [f"Hey! I'm {chief.label}, your {chief.title}." if named else f"Hey! I'm your {chief.title}.",
                "Message me anytime. I'll keep things moving, even when we're not talking, and check in with updates or "
                "questions."]
        if not named:
            said.append("Want to give me a name?")
        for text in said:
            self._record(chat.id, kind="text", data={"text": text, "greeting": True})
        self._record(chat.id, kind="onboarding", data={"offer": "bring_in"})
        self.store.update_task(chat.id, briefing=prompts.text(
            "greeted", self.crew.home, said="\n\n".join(said),
            naming="" if named else " When they give you a name, take it with `set_name`."))
        return chat

    def bring_in(self, projects: list[dict[str, Any]],
                 memories: list[discovery.FoundMemory]) -> tuple[list[Project], Task | None]:
        """Adds the folders the user picked as projects, and has the chief learn about them and from the memories
        they picked, in a thread of its own. The crew's started off then."""
        added = [projects_module.save(self.crew.home, p["name"], Path(p["folder"]), p.get("about", "")) for p in projects]
        if added:
            loaded, errors = projects_module.load(self.crew.home)
            self.reload(dataclasses.replace(self.crew, projects=loaded, project_errors=errors))
        self.store.routine_ran(STARTED, _now(), "")
        chief = self.coordinator()
        if chief is None:
            return added, None
        chat = self.chat(chief.name)
        thread = None
        if memories or added:
            text, taken = discovery.digest(memories)
            thread = self.submit(chief.name, prompts.text("bring-in", self.crew.home, memories=text or "(none)"),
                                 parent_id=chat.id, title="Learn what Claude Code and Codex know", said_by="crewbots")
            self.store.mark_brought_in({str(path): print_ for path, print_ in taken.items()})
            self.store.routine_ran(STARTED, _now(), thread.id)
        self._record(chat.id, kind="onboarding", data={"brought": {
            "projects": [p.name for p in added], "memories": len(memories), "thread": thread.id if thread else None}})
        return added, thread

    def brought_in(self, finished: list[Task]) -> str | None:
        """What the chief's chat hears when the thread it learned in finishes: what it found, and to tell the user,
        then ask where to start. None for any other thread."""
        learned = (self.store.routine_state(STARTED) or {}).get("last_task_id")
        if len(finished) != 1 or not learned or finished[0].id != learned:
            return None
        return prompts.text("brought-in", self.crew.home, result=(finished[0].result or "").strip() or "(nothing)")

    def not_now(self) -> None:
        """The user would rather start without bringing anything in: the offer isn't made again."""
        self.store.routine_ran(STARTED, _now(), "")
        if chief := self.coordinator():
            chat = self.chat(chief.name)
            if self.store.last_events(chat.id, ("onboarding",), 1):
                self._record(chat.id, kind="onboarding", data={"declined": True})

    def set_name(self, task_id: str, name: str) -> Bot:
        """The `set_name` tool: a bot takes the name the user gives it, in a conversation with them (its chat or a
        group chat). Only its own: no bot names another."""
        task = self.store.get_task(task_id)
        if task.kind not in ("chat", "seat"):
            raise NotAllowed("you take a name in a conversation with the user: your chat, or a group chat")
        name = " ".join(str(name).split())
        if not name or len(name) > NAME_CHARS or "@" in name:
            raise CrewError(f"a name is a word or a few, up to {NAME_CHARS} characters, without @")
        if any(name.lower() in (b.name, b.label.lower()) for b in self.crew.bots.values() if b.name != task.bot):
            raise CrewError(f"a teammate goes by {name} already")
        cards.save_card(self.crew.home, task.bot, {"display_name": name}, None)
        self.reload(crew_module.load(self.crew.home))
        return self.crew.bots[task.bot]
