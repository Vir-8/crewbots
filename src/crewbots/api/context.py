"""What every group of routes works with: the daemon's runner, store, scheduler and code review, and the few helpers
more than one group needs."""

import asyncio
import functools
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from crewbots import attachments as attachments_module
from crewbots import crew as crew_module
from crewbots import paths
from crewbots.api.schemas import Message
from crewbots.reviews import Reviews
from crewbots.runner import Runner
from crewbots.scheduler import Scheduler
from crewbots.store import Store, Task

API_VERSION = 1  # the /v1 in every route; a change the app can't follow raises it
PROJECT = Path(__file__).resolve().parents[3]  # the checkout the daemon runs from, when it runs from one


def checkout_commit() -> str | None:
    """The commit the daemon's checkout is at, or None when it isn't one (an installed package)."""
    if not (PROJECT / ".git").exists():
        return None
    try:
        done = subprocess.run([paths.git(), "-C", str(PROJECT), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                              text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() or None


@functools.cache
def started_from() -> str | None:
    """The commit the daemon's code came from: its checkout's when it first asked, as it started. What's committed
    after that runs once it restarts."""
    return checkout_commit()


@dataclass
class Context:
    runner: Runner
    store: Store
    scheduler: Scheduler
    reviews: Reviews
    app: FastAPI
    allowed: Callable[[str | None, str | None], bool]  # whether a host and credential may use the user's API
    starting: set[asyncio.Task] = field(default_factory=set)  # reviews getting their code, kept until they've started

    def task_or_404(self, task_id: str) -> Task:
        try:
            return self.store.get_task(task_id)
        except KeyError:
            raise HTTPException(404, f"no task {task_id}") from None

    def bot_or_404(self, name: str) -> crew_module.Bot:
        try:
            return self.runner.crew.bots[name]
        except KeyError:
            raise HTTPException(404, f"no bot named {name!r}") from None

    def attached(self, body: Message) -> list[dict[str, Any]]:
        """A message's attachments, found in the crew's folder; a message needs text or one of them."""
        if not body.text.strip() and not body.attachments:
            raise HTTPException(422, "a message needs text, or an attachment")
        return [attachments_module.find(self.runner.crew.home, a) for a in body.attachments]

    def reload_crew(self) -> None:
        self.runner.reload(crew_module.load(self.runner.crew.home))
