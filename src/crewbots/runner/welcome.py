"""Getting a crew started: the chief's first word in its chat, and bringing in what Claude Code and Codex already know
(the folders the user works in, as projects, and their memories). Both happen once, until the crew's been started off.

The greeting is the chief's own messages in its chat, kept like any others, with an offer under them that remembers
how the user answered it. What the chief learns from the memories it learns in a thread of its own, so the chat stays
a conversation; the thread's result comes back to the chat, as a thread's does.
"""

import dataclasses
from pathlib import Path
from typing import Any

from crewbots import discovery, prompts
from crewbots import projects as projects_module
from crewbots.crew import Bot
from crewbots.projects import Project
from crewbots.store import Task
from crewbots.store.records import _now

STARTED = "started"  # in the routines table: when the crew was started off, which happens once


class Welcome:
    """The part of Runner that starts a crew off."""

    def coordinator(self) -> Bot | None:
        bots = self.crew.bots.values()
        return next((b for b in bots if b.can_delegate), next(iter(bots), None))

    def started(self) -> bool:
        return bool(self.crew.projects) or self.store.routine_state(STARTED) is not None

    def greet(self) -> Task | None:
        """The chief's first word in its chat, once: who it is, that it keeps things moving, and an offer to bring
        in what Claude Code and Codex know. Not for a crew that's been started off."""
        chief = self.coordinator()
        if chief is None or self.started():
            return None
        chat = self.chat(chief.name)
        if any(e.kind == "onboarding" for e in self.store.events(chat.id)):
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
        return chat

    def bring_in(self, projects: list[dict[str, Any]],
                 memories: list[discovery.FoundMemory]) -> tuple[list[Project], Task | None]:
        """Adds the folders the user picked as projects, and has the chief learn from the memories they picked, in a
        thread of its own. The crew's started off then."""
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
        if memories:
            thread = self.submit(chief.name, prompts.text("bring-in", self.crew.home, memories=discovery.digest(memories)),
                                 parent_id=chat.id, title="Learn what Claude Code and Codex know", said_by="crewbots")
        self._record(chat.id, kind="onboarding", data={"brought": {
            "projects": [p.name for p in added], "memories": len(memories), "thread": thread.id if thread else None}})
        return added, thread

    def not_now(self) -> None:
        """The user would rather start without bringing anything in: the offer isn't made again."""
        self.store.routine_ran(STARTED, _now(), "")
        if chief := self.coordinator():
            chat = self.chat(chief.name)
            if any(e.kind == "onboarding" for e in self.store.events(chat.id)):
                self._record(chat.id, kind="onboarding", data={"declined": True})
