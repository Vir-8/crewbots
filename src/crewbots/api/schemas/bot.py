"""What a bot sends to the /bot/ API, through its crewbots MCP server."""


from pydantic import BaseModel

from crewbots.store import Verdict


class Delegation(BaseModel):
    bot: str
    goal: str
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

class Escalation(BaseModel):
    question: str

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

class Forget(BaseModel):
    name: str
    shared: bool = False

class ThreadBody(BaseModel):
    title: str
    goal: str

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
