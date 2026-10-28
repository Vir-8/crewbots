"""What a bot sends to the /bot/ API, through its crewbots MCP server."""

from typing import Literal

from pydantic import BaseModel

from crewbots.store import Verdict


class Delegation(BaseModel):
    bot: str
    goal: str
    project: str | None = None
    workspace: str | None = None
    review_of: str | None = None

class AskBody(BaseModel):
    bot: str
    question: str


class OpenGroup(BaseModel):
    title: str
    members: list[str]
    lead: str | None = None


class GroupMember(BaseModel):
    bot: str
    room: str | None = None  # the group chat the bot is talking in, if left out


class ChildMessage(BaseModel):
    task_id: str
    text: str

class HandoffBody(BaseModel):
    done: str
    output: list[str] = []
    open: str = ""
    needs: str = ""

class Choice(BaseModel):
    label: str
    description: str | None = None

class Escalation(BaseModel):
    question: str
    choices: list[Choice] = []  # what the user can pick from; they can always answer in their own words
    multiple: bool = False  # whether they may pick several

class Finding(BaseModel):
    location: str
    problem: str
    scenario: str = ""

class ReviewBody(BaseModel):
    verdict: Verdict
    summary: str
    findings: list[Finding] = []

class Remember(BaseModel):
    title: str
    body: str
    shared: bool = False

class NextCheck(BaseModel):
    minutes: float
    why: str

class NameBody(BaseModel):
    name: str

class PageWrite(BaseModel):
    page: str
    text: str

class PageEdit(BaseModel):
    page: str
    find: str
    replace: str

class PermissionAsk(BaseModel):
    what: str  # what it'd do, in a line the user can say yes to
    why: str = ""
    kind: Literal["action", "folder", "network"] = "action"
    folder: str | None = None  # for a folder: its whole path

class Forget(BaseModel):
    name: str
    shared: bool = False

class ThreadBody(BaseModel):
    title: str
    goal: str
    project: str | None = None

class PostBody(BaseModel):
    text: str
    room: str | None = None


class ShareBody(BaseModel):
    files: list[str]
    text: str = ""
    room: str | None = None

class Recall(BaseModel):
    query: str

class ProposedRoutine(BaseModel):
    name: str
    description: str
    goal: str
    schedule: str | None = None
    on: str | None = None
    repo: str | None = None


class RemindOn(BaseModel):
    event: str
    match: str = ""
    then: str


class StateBody(BaseModel):
    state: str


class SearchHit(BaseModel):
    task_id: str
    kind: str
    at: str
    snippet: str
    bot: str
    title: str

class MergeProposal(BaseModel):
    task_id: str
    summary: str


class MergeWithdrawal(BaseModel):
    task_id: str
    reason: str


class TaskDrop(BaseModel):
    task_id: str
    reason: str


class TaskStop(BaseModel):
    task_id: str


class WithdrawalReason(BaseModel):
    reason: str
