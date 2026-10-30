"""What a bot sends to the /bot/ API, through its crewbots MCP server: the crew tools' own arguments
(crewbots.tool_args), so Codex's bots are read as Claude's are, and what the API sends back."""

from pydantic import BaseModel

from crewbots.tool_args import (  # noqa: F401 (the API's models, by name)
    AskBody,
    ChildMessage,
    Choice,
    Delegation,
    Escalation,
    Finding,
    Forget,
    GroupMember,
    HandoffBody,
    MergeProposal,
    MergeWithdrawal,
    NameBody,
    NextCheck,
    OpenGroup,
    PageEdit,
    PageWrite,
    PermissionAsk,
    PostBody,
    ProposedRoutine,
    Recall,
    Remember,
    RemindOn,
    ReviewBody,
    ShareBody,
    StateBody,
    TaskDrop,
    TaskStop,
    TellBody,
    ThreadBody,
)


class SearchHit(BaseModel):
    task_id: str
    kind: str
    at: str
    snippet: str
    bot: str
    title: str


class WithdrawalReason(BaseModel):
    reason: str
