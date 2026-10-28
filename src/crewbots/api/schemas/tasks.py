"""Tasks: what they are, their events, status and children, and the messages sent to them."""

from typing import Any, Literal

from pydantic import BaseModel

from crewbots.store import Status, Verdict


class RetryOut(BaseModel):
    """A task waiting to try again after a failed turn: the try it's waiting for, of how many."""

    attempt: int
    of: int
    wait_seconds: float


class TaskOut(BaseModel):
    id: str
    bot: str
    title: str
    status: Status
    created_by: str
    parent_id: str | None
    workspace: str | None
    repo: str | None
    worktree_path: str | None
    branch: str | None
    base: str | None
    session_id: str | None
    result: str | None
    error: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    reported: bool
    turns: int
    review_of: str | None
    review_version: int | None
    merge_state: Literal["proposed", "merging", "merged"] | None
    merged_commit: str | None
    merged_tip: str | None
    kind: Literal["chat", "seat", "thread", "review", "ask"]
    room_id: str | None
    pr: str | None  # a code review's pull request, owner/name#number
    routine: str | None
    budget: int | None
    created_at: str
    updated_at: str
    attempt: RetryOut | None = None  # set while it waits between tries


class MergeResultOut(TaskOut):
    leftover_files: list[str] | None = None
    leftover_check_error: str | None = None


class MergeStatusOut(BaseModel):
    already_on: str | None  # the branch a proposal's changes are already part of


class WithdrawalResultOut(TaskOut):
    notice: str | None = None


class EventOut(BaseModel):
    id: int
    task_id: str
    kind: str  # a backend event (text, tool_call, ...) or a crewbots one (message, handoff, review, ...)
    data: dict[str, Any]
    at: str

class TaskStatus(BaseModel):
    """What /status shows for a chat or a thread."""

    task_id: str
    bot: str
    backend: str | None
    plan: str | None  # claude or codex: whose limits it uses
    model: str | None  # None: the CLI's own default
    effort: str | None
    session_id: str | None
    open: bool  # its session is open now; a closed one resumes from session_id
    context_percent: float | None  # how full its context is, when last measured
    context_window: int | None  # the tokens it's measured against; None: the model's whole window
    rollover_percent: float | None  # past this, it's flushed to memory and compacted
    turns: int
    background: str | None = None  # whether the crew's background work runs on its plan now, and why

class FindingOut(BaseModel):
    location: str | None = None
    problem: str | None = None
    scenario: str | None = None

class ReviewEntryOut(BaseModel):
    task_id: str
    reviewer: str
    status: Status
    verdict: Verdict | None
    summary: str | None
    findings: list[FindingOut]

class ReviewStateOut(BaseModel):
    work_id: str
    version: int
    round: int
    max_rounds: int
    approved: bool
    failed: bool
    out_of_rounds: bool
    reviews: list[ReviewEntryOut]

class HandoffOut(BaseModel):
    id: int
    task_id: str
    from_bot: str
    done: str
    output: list[str]
    open: str
    needs: str
    at: str

class ChildOut(BaseModel):
    id: str
    bot: str
    title: str
    status: Status
    worktree: str | None = None
    branch: str | None = None
    handoff: HandoffOut | None = None
    result: str | None = None
    error: str | None = None

class CursorOut(BaseModel):
    id: int

class ChangesOut(BaseModel):
    diff: str | None

class NewTask(BaseModel):
    bot: str
    goal: str
    workspace: str | None = None

class Message(BaseModel):
    text: str = ""
    attachments: list[str] = []  # ids from POST /attachments
    question_id: int | None = None  # original seat escalation event

class MergeRequest(BaseModel):
    force: bool = False

class PermissionAnswer(BaseModel):
    allowed: bool

class PermissionOut(BaseModel):
    id: int
    task_id: str
    kind: str  # action, folder or network
    folder: str
    what: str
    why: str
    asked_at: str
    allowed: bool | None  # none until the user answers
    answered_at: str | None
