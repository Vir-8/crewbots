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
class Done:
    """Always the last event of a run, exactly once."""

    final_text: str
    is_error: bool = False
    error: str | None = None
    kind: str = field(default="done", init=False)


Event = SessionStarted | Text | ToolCall | ToolResult | Usage | Notice | Done
