"""The crew as a whole: its state, the daemon's version, restarting, the app's errors, and the agent log."""

from typing import Literal

from pydantic import BaseModel

RESTART_TIMEOUT_S = 20 * 60.0  # how long a restart waits for running turns; then they're cut off and carried on



class PlanLimitOut(BaseModel):
    plan: str
    window: str
    status: Literal["ok", "warning", "limited"]
    used_percent: float | None
    resets_at: int | None

class UpdateCommit(BaseModel):
    sha: str
    at: str
    subject: str


class ReleaseOut(BaseModel):
    """How the last update went, as scripts/release left it."""

    state: str | None = None  # running, done or failed
    step: str | None = None
    commit: str | None = None  # what it releases
    started_at: int | None = None
    at: int | None = None
    log: str | None = None  # the end of its log, when it failed
    bundle: str | None = None  # the app it built, for the app to install ("built")
    expected: int | None = None  # seconds the last update like it took, to say how far along this one is
    kind: str | None = None  # app (built again, and the daemon restarted) or daemon (restarted only)


class UpdateNeeds(BaseModel):
    """Which parts an update needs: the app built again (its code changed since it was built), the daemon restarted
    (its code changed since it started)."""

    app: bool = False
    daemon: bool = False


class UpdateOut(BaseModel):
    """What updating crewbots from its checkout would bring, and how the last update went."""

    app: str | None  # the commit the asking app was built from
    daemon: str | None  # the commit the daemon started from
    head: str | None  # the checkout's
    available: bool
    commits: list[UpdateCommit]  # what's new, newest first
    pull: bool  # it comes from GitHub, as a fast-forward
    note: str | None  # why there's nothing to update to, when there's a reason
    needs: UpdateNeeds = UpdateNeeds()
    release: ReleaseOut


class UpdateBody(BaseModel):
    app: str | None = None  # the commit the app was built from


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

class BackgroundOut(BaseModel):
    """Whether the crew's background work may run on a plan now, and why (`capacity.py`)."""

    plan: str
    go: bool
    reason: str


class CrewState(BaseModel):
    paused: bool
    restarting: bool  # the daemon restarts once no turn is running; meanwhile none starts
    running: int
    turns_running: int  # under way now, not waiting their turn or held
    held: int
    sessions: int
    limits: list[PlanLimitOut]
    background: list[BackgroundOut] = []

class AgentLogOut(BaseModel):
    """One thing a bot did to another: delegated, asked for a review, asked, messaged, posted, or opened or grew a
    group chat; or a tool call of a bot's that crewbots refused."""

    id: int
    at: str
    kind: Literal["delegate", "review", "ask", "message", "post", "open_group", "add_to_group", "refused"]
    bot: str
    target: str | None  # the bot it was to, if one
    task_id: str | None
    room_id: str | None
    text: str

class Consolidate(BaseModel):
    bot: str
    weekly: bool | None = None  # also merge duplicate notes; None: only on Sundays, as overnight

class CrewEventOut(BaseModel):
    """Something that happened around the crew: a commit, a branch, a changed file, a review request, checks that
    failed or passed, a thread that finished."""

    id: int
    at: str
    kind: str
    subject: str
    text: str

class IntentOut(BaseModel):
    """A standing intent: when an event of `kind` mentioning `match` happens, its bot is told `text`."""

    id: int
    bot: str
    task_id: str | None
    kind: str
    match: str
    text: str
    created_at: str
    expires_at: str
    fires: int
    last_fired_at: str | None
    cancelled: bool

class ProactiveOut(BaseModel):
    """Something the crew raised on its own, and how it reached the user."""

    id: int
    at: str
    bot: str
    level: Literal["fyi", "question", "needs_you"]
    text: str
    source: str
    expires_at: str
    delivered_at: str | None
    digest_at: str | None
    reaction: Literal["up", "down"] | None

class Reaction(BaseModel):
    reaction: Literal["up", "down"]

class DigestOut(BaseModel):
    bot: str  # whose chat it's in: the chief's
    items: int
