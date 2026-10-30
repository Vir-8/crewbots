"""Bots: who they are, their role cards, their profiles, and the models they can run on."""

from typing import Literal

from pydantic import BaseModel

from crewbots.api.schemas.tasks import TaskOut


class BotOut(BaseModel):
    name: str
    title: str
    description: str
    backend: str
    model: str | None
    access: str
    workspace: str | None
    network: bool | list[str]  # its shell commands: none, any host, or these hosts
    mcp: list[str]  # the MCP servers its role card grants, besides crewbots' own
    skills: list[str]
    can_delegate: bool
    path: str | None  # its role card
    display_name: str | None  # what you call it; `name` is its handle
    effort: str | None  # reasoning effort; none: the CLI's default
    avatar: dict[str, str] | None  # {color, shape}; none: picked from its name
    context_window: int | None  # tokens its sessions hold before compacting; none: the model's whole window
    proactive: bool = True  # a check-in of its may start low-risk work on its own

class Avatar(BaseModel):
    color: str | None = None
    shape: str | None = None

class Budget(BaseModel):
    tokens: int
    per: Literal["day", "week"] = "week"

class PageOut(BaseModel):
    name: str
    path: str
    updated: str
    text: str | None = None  # left out when listing

class PageBody(BaseModel):
    text: str

class CardBody(BaseModel):
    """A role card's editable parts. Fields left out are left as they are."""

    display_name: str | None = None
    title: str | None = None
    description: str | None = None
    backend: str | None = None
    model: str | None = None
    effort: str | None = None
    context_window: int | Literal["auto"] | None = None  # tokens, or the model's whole window; blank: the default
    access: Literal["read_only", "workspace_write", "full"] | None = None
    network: bool | list[str] | None = None
    avatar: Avatar | None = None
    budget: Budget | None = None
    mcp: list[str] | None = None  # the crew's MCP servers (mcp.json) it may use
    proactive: bool | None = None  # whether a check-in may start low-risk work on its own
    instructions: str | None = None  # the card's body: mission, what it owns, what done means

class NewBot(CardBody):
    name: str  # its handle

class CardOut(CardBody):
    name: str

class ModelOut(BaseModel):
    id: str
    label: str
    description: str
    efforts: list[str]
    default_effort: str | None
    default: bool

class ModelsOut(BaseModel):
    claude: list[ModelOut]
    codex: list[ModelOut]
    antigravity: list[ModelOut] = []

class OutputOut(BaseModel):
    path: str
    task_id: str
    title: str
    at: str

class ProfileOut(BaseModel):
    bot: str
    working: bool
    last_active: str | None
    threads: list[TaskOut]
    outputs: list[OutputOut]
