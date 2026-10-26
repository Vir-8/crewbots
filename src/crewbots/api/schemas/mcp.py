"""The crew's MCP servers and skills, and where to add more from."""

from typing import Literal

from pydantic import BaseModel


class McpServerOut(BaseModel):
    """One of the crew's MCP servers, without the values of its env or headers, which can be keys."""

    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None
    args: list[str]
    url: str | None
    env_keys: list[str]
    header_keys: list[str]
    bots: list[str]  # the bots whose cards name it

class McpServerBody(BaseModel):
    transport: Literal["stdio", "http", "sse"] = "stdio"
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] | None = None  # None: keep what it has
    url: str | None = None
    headers: dict[str, str] | None = None  # None: keep what it has

class InstalledServer(BaseModel):
    """A server one of your other apps has, that the crew could copy."""

    id: str
    source: str  # "Claude Code", "Claude Code · <project>", "Claude desktop" or "Codex"
    name: str
    transport: Literal["stdio", "http", "sse"]
    command: str | None
    args: list[str]
    url: str | None
    env_keys: list[str]
    header_keys: list[str]
    in_crew: bool  # the crew has a server by that name already

class ImportBody(BaseModel):
    id: str
    name: str | None = None  # to add it under another name

class RegistryField(BaseModel):
    kind: Literal["env", "arg", "header"]
    key: str
    description: str
    required: bool
    secret: bool
    default: str | None

class RegistryOption(BaseModel):
    id: str  # package:<n> or remote:<n>
    label: str
    fields: list[RegistryField]

class RegistryEntry(BaseModel):
    name: str
    title: str | None
    description: str
    version: str
    website: str | None
    options: list[RegistryOption]

class RegistryAdd(BaseModel):
    server: str  # its name in the registry
    version: str
    option: str
    name: str  # what the crew calls it
    values: dict[str, str] = {}

class SkillOut(BaseModel):
    name: str
    description: str
    source: str  # "crew", or the global folder it's in, such as ~/.claude/skills
    path: str
    used: bool  # False: a skill of the same name was found first, and that one is used
    same_as: str | None  # for one not used, where the one used is

class McpCheck(BaseModel):
    ok: bool
    tools: list[str]
    error: str | None
