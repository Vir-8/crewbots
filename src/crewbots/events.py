"""The one event stream every backend is translated into. The app, the store and the crew only ever see these."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SessionStarted:
    """The backend's own session id, kept so a later run can resume the conversation."""

    session_id: str
    kind: str = field(default="session_started", init=False)


@dataclass(frozen=True)
class Text:
    text: str
    kind: str = field(default="text", init=False)


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]
    kind: str = field(default="tool_call", init=False)


@dataclass(frozen=True)
class ToolResult:
    id: str
    output: str
    is_error: bool = False
    kind: str = field(default="tool_result", init=False)


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0
    cost_usd: float | None = None  # what the CLI reports; on a subscription it is notional, not billed
    kind: str = field(default="usage", init=False)


@dataclass(frozen=True)
class Notice:
    """Something the backend said that is worth showing but is not fatal: a retry, a warning, a stray stderr line."""

    message: str
    kind: str = field(default="notice", init=False)


@dataclass(frozen=True)
class RateLimit:
    """Where the subscription plan's usage limit stands, as the CLI reports it: `ok`, `warning` (getting close), or
    `limited` (reached, until `resets_at`)."""

    plan: str  # claude or codex: the limit is the plan's, shared by every bot on it
    window: str  # which limit, e.g. five_hour or seven_day
    status: str
    used_percent: float | None = None
    resets_at: int | None = None  # unix time
    kind: str = field(default="rate_limit", init=False)


@dataclass(frozen=True)
class Compacted:
    """The CLI compacted the conversation: on its own (`auto`), or because crewbots asked (`manual`). The token counts
    are the backend's, where it gives them."""

    trigger: str
    before: int | None = None
    after: int | None = None
    kind: str = field(default="compacted", init=False)


@dataclass(frozen=True)
class Done:
    """Always the last event of a run, exactly once. A failure the backend knows is passing says so in `retry`:
    `transient` (the service was down or overloaded, the connection dropped, the CLI died) or `limit` (a rate or plan
    limit), and the runner tries the turn again."""

    final_text: str
    is_error: bool = False
    error: str | None = None
    retry: str | None = None
    kind: str = field(default="done", init=False)


Event = SessionStarted | Text | ToolCall | ToolResult | Usage | RateLimit | Notice | Compacted | Done

BY_KIND: dict[str, type[Event]] = {
    cls.__dataclass_fields__["kind"].default: cls
    for cls in (SessionStarted, Text, ToolCall, ToolResult, Usage, RateLimit, Notice, Compacted, Done)
}


def from_json(kind: str, data: dict[str, Any]) -> Event | None:
    """Rebuilds a stored event; None for kinds that are not backend events (messages, worktrees). What the store
    keeps beside an event's own fields (a usage event's plan, model and effort) is left out."""
    cls = BY_KIND.get(kind)
    if cls is None:
        return None
    fields = {name for name, f in cls.__dataclass_fields__.items() if f.init}
    return cls(**{k: v for k, v in data.items() if k in fields})
