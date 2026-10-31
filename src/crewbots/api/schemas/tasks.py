"""Tasks: what they are, their events, status and children, and the messages sent to them."""

from typing import Any

from pydantic import BaseModel

from crewbots.store import Kind, MergeState, Status, Verdict


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
    merge_state: MergeState | None
    merged_commit: str | None
    merged_tip: str | None
    kind: Kind
    room_id: str | None
    pr: str | None  # a code review's pull request, owner/name#number
    routine: str | None
    budget: int | None
    created_at: str
    updated_at: str
    attempt: RetryOut | None = None  # set while it waits between tries


class MergedOut(TaskOut):
    ignored: list[str] = []  # output it handed back that git ignores in the project, so the merge left it out


class MergeStatusOut(BaseModel):
    already_on: str | None  # the branch a proposal's changes are already part of


class LandedVerdict(BaseModel):
    bot: str
    verdict: str
    summary: str


class LandedItem(BaseModel):
    """Work that merged, with what it merged on: its handback's evidence and the verdicts of the version that merged."""

    task: TaskOut
    merged_at: str
    commit: str | None
    into: str | None  # the folder it merged into
    automatic: bool  # merged by crewbots once its reviews passed, rather than by the user
    evidence: list[str]
    verdicts: list[LandedVerdict]
    reverted: dict[str, Any] | None  # its revert's commit, once the user reverted it


class LandedOut(BaseModel):
    items: list[LandedItem]
    seen_at: str | None  # when the user last looked


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

class SearchHit(BaseModel):
    task_id: str
    kind: str
    at: str
    snippet: str
    bot: str
    title: str

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

class WithdrawalReason(BaseModel):
    reason: str

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
