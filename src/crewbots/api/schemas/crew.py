"""The crew as a whole: its state, the daemon's version, restarting, and the app's errors."""

from typing import Literal

from pydantic import BaseModel

RESTART_TIMEOUT_S = 20 * 60.0  # how long a restart waits for running turns; then they're cut off and carried on



class PlanLimitOut(BaseModel):
    plan: str
    window: str
    status: Literal["ok", "warning", "limited"]
    used_percent: float | None
    resets_at: int | None

class VersionOut(BaseModel):
    """What the daemon is, so the app can tell when one of them is out of date."""

    api: int  # the major version: the app and the daemon must agree on it
    schema_hash: str  # a fingerprint of every route and model; the app is built with its own
    build: str | None  # the commit the daemon started from, when it runs from a git checkout
    source_changed: bool  # the checkout has moved on since: a restart would update the daemon

class ClientError(BaseModel):
    """Something in the app's window that failed, for the daemon's log: the app has no terminal of its own."""

    where: str
    message: str
    stack: str | None = None

class RestartBody(BaseModel):
    when_idle: bool = True
    timeout_s: float = RESTART_TIMEOUT_S

class CrewState(BaseModel):
    paused: bool
    restarting: bool  # the daemon restarts once no turn is running; meanwhile none starts
    running: int
    turns_running: int  # under way now, not waiting their turn or held
    held: int
    sessions: int
    limits: list[PlanLimitOut]
