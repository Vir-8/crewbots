"""What each crew tool takes, as pydantic models: the JSON schema a bot is shown, and how what it sends is read. Both
ways a bot reaches the tools use them, the in-process ones (crew_tools) and the /bot/ API behind Codex's MCP server.

They read what bots send, not only what the schema says: a list given as one string of a line each, and a name a bot
reaches for when the field has another (`id` for working_state's entry_id). A name a tool doesn't take is left out,
and the bot is told which, rather than the call going on as if it had never been given.
"""

from functools import cache
from typing import Annotated, Any, ClassVar, Literal

from pydantic import AliasChoices, BaseModel, BeforeValidator, Field, ValidationError, WithJsonSchema, model_validator

from crewbots.store import Verdict


def lines(given: Any) -> list[str]:
    """A list a bot gave, or the string it gave in its place, as items: one per line, without a leading "- "."""
    if isinstance(given, str):
        return [line.strip().removeprefix("- ").strip() for line in given.splitlines() if line.strip()]
    return [str(item) for item in given or [] if str(item).strip()]


# A list, or a string of a line each: with one item, or a few commands, a bot often sends it bare
Lines = Annotated[list[str], BeforeValidator(lines),
                  WithJsonSchema({"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}]})]


def also(name: str, *others: str, default: Any = ...) -> Any:
    """A field a bot may send under other names too, the ones it reaches for."""
    return Field(default, validation_alias=AliasChoices(name, *others))


class Args(BaseModel):
    """A crew tool's arguments."""

    may_be_empty: ClassVar[frozenset[str]] = frozenset()  # required, but an empty string is a value


class Forwarded(Args):
    """Arguments passed on with only what the bot gave, so what they're passed to applies its own defaults."""


class NoArgs(Args):
    pass


class BrowserOpen(Forwarded):
    url: str


class BrowserScreenshot(Forwarded):
    width: int | None = None
    height: int | None = None
    theme: Literal["light", "dark"] | None = None
    full_page: bool | None = None


class BrowserClick(Forwarded):
    selector: str


class BrowserType(Forwarded):
    selector: str | None = None
    text: str | None = None
    key: str | None = None


class BrowserEvaluate(Forwarded):
    expression: str = also("expression", "script")


class BrowserConsole(Forwarded):
    clear: bool | None = None


class WebKitCheck(Forwarded):
    url: str
    click: str | None = None
    setup: str | None = None
    js: str | None = None
    wait: float | None = None
    width: int | None = None
    height: int | None = None


class HandoffBody(Args):
    done: str
    output: Lines = []
    open: str = ""
    needs: str = ""
    evidence: Lines = []  # what was run or seen to prove it works, and what it showed
    checks: Lines = []  # commands that prove it, which crewbots reruns before a review


class Choice(BaseModel):
    label: str
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _bare(cls, given: Any) -> Any:
        return {"label": given} if isinstance(given, str) else given


class Escalation(Args):
    question: str
    choices: list[Choice] = []  # what the user can pick from; they can always answer in their own words
    multiple: bool = False  # whether they may pick several


class PermissionAsk(Args):
    what: str  # what it'd do, in a line the user can say yes to
    why: str = ""
    kind: Literal["action", "folder", "network"] = "action"
    folder: str | None = None  # for a folder: its whole path
    task_id: str | None = None  # a teammate's task it's for, when the chief asks on its behalf


class Finding(BaseModel):
    location: str
    problem: str
    scenario: str = ""
    proof: str = ""  # the command the reviewer ran and what it showed; none: not reproduced


class ReviewBody(Args):
    verdict: Annotated[Verdict, BeforeValidator(lambda v: v.lower() if isinstance(v, str) else v)]
    summary: str
    findings: list[Finding] = []


class Remember(Args):
    title: str
    body: str
    shared: bool = False
    mistake: str = ""  # when the user corrected you: what you got wrong, counted for the weekly retro
    check: str = ""  # a test, lint rule or CI check that would have caught it


class PageWrite(Args):
    page: str
    content: str = also("content", "text")


class PageEdit(Args):
    may_be_empty = frozenset({"replace"})  # replaced with nothing: taken out
    page: str
    find: str = also("find", "old")
    replace: str = also("replace", "new")


class Forget(Args):
    name: str
    shared: bool = False


class Recall(Args):
    query: str


class WorkingStateEdit(Args):
    section: str = ""
    action: Annotated[Literal["list", "add", "replace", "remove"], BeforeValidator(lambda v: v or "list")] = "list"
    text: str = ""
    entry_id: str = also("entry_id", "id", default="")
    done: bool = False


class StateBody(Args):
    state: str


class NameBody(Args):
    name: str


class RemindOn(Args):
    event: str
    match: str = ""
    then: str


class ProposedRoutine(Args):
    name: str
    description: str
    goal: str
    schedule: str | None = None
    on: str | None = None
    repo: str | None = None


class ThreadBody(Args):
    title: str
    goal: str
    project: str | None = None
    in_folder: bool = False


class PostBody(Args):
    text: str
    room: str | None = None


class TellBody(Args):
    text: str
    needs_you: bool = False


class ShareBody(Args):
    files: Lines = also("files", "path", "file")
    text: str = ""
    room: str | None = None


class AskBody(Args):
    bot: str
    question: str


class OpenGroup(Args):
    title: str
    members: list[str]
    lead: str | None = None


class GroupMember(Args):
    bot: str
    room: str | None = None  # the group chat the bot is talking in, if left out


class AddBot(Forwarded):
    name: str
    title: str
    description: str
    instructions: str
    display_name: str | None = None
    backend: Literal["claude", "codex", "antigravity"] | None = None
    model: str | None = None
    effort: str | None = None
    # Shown as a choice of these, but any is read: the runner refuses full access with why
    access: str | None = Field(None, json_schema_extra={"enum": ["read_only", "workspace_write"]})
    network: bool | None = None
    coordinator: bool | None = None
    mcp: list[str] | None = None
    skills: list[str] | None = None


class Delegation(Args):
    bot: str
    goal: str
    project: str | None = None
    workspace: str | None = None
    review_of: str | None = None
    in_folder: bool = False


class RoomStatus(Args):
    room: str | None = None


class ChildMessage(Args):
    task_id: str
    text: str


class TaskStop(Args):
    task_id: str


class PushProposal(Args):
    project: str
    summary: str


class MainReplacement(Args):
    project: str
    branch: str
    summary: str


class MergeProposal(Args):
    task_id: str
    summary: str


class MergeWithdrawal(Args):
    task_id: str
    reason: str


class TaskDrop(Args):
    task_id: str
    reason: str


class WorktreeSweep(Args):
    dry_run: bool = True


class NextCheck(Args):
    minutes: float
    why: str


class ArgsError(ValueError):
    """What a bot sent that its tool can't take, said for the bot."""


def read(name: str, model: type[Args], given: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """What a bot sent the tool `name`, as its arguments by field name with the defaults of those it left out (for a
    `Forwarded` tool, only those it gave), in JSON's types; and the names it sent that the tool doesn't take."""
    try:
        args = model.model_validate(given).model_dump(mode="json", exclude_unset=issubclass(model, Forwarded))
    except ValidationError as e:
        raise ArgsError(_said(name, e)) from None
    if empty := [key for key, field in model.model_fields.items()
                 if field.is_required() and key not in model.may_be_empty and args.get(key) in ("", [])]:
        raise ArgsError(f"{name} needs {', '.join(empty)}")
    return args, [key for key in given if key not in _names(model)]


def left_out(name: str, model: type[Args], unknown: list[str]) -> str:
    them = " or ".join(f"`{key}`" for key in unknown)
    return (f"({name} takes no {them}, so {'it was' if len(unknown) == 1 else 'they were'} left out. It takes "
            f"{', '.join(model.model_fields) or 'nothing'}.)")


def _said(name: str, error: ValidationError) -> str:
    missing = [str(e["loc"][0]) for e in error.errors() if e["type"] == "missing"]
    wrong = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors() if e["type"] != "missing"]
    return "; ".join([f"{name} needs {', '.join(missing)}"] * bool(missing) + [f"{name}: {'; '.join(wrong)}"] * bool(wrong))


def _names(model: type[Args]) -> set[str]:
    names = set(model.model_fields)
    for field in model.model_fields.values():
        if isinstance(field.validation_alias, AliasChoices):
            names |= {choice for choice in field.validation_alias.choices if isinstance(choice, str)}
    return names


@cache
def schema(model: type[Args]) -> dict[str, Any]:
    """The JSON schema a bot is shown for `model`: one object with nothing nested by reference, and no titles or
    defaults, since every tool's schema is sent with every turn."""
    full = model.model_json_schema()
    defs = full.pop("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return clean(defs[node["$ref"].rsplit("/", 1)[1]])
        if len(kinds := [k for k in node.get("anyOf", []) if k != {"type": "null"}]) == 1:  # optional: null not sent
            node = {**{k: v for k, v in node.items() if k != "anyOf"}, **kinds[0]}
        return {key: ({name: clean(field) for name, field in value.items()} if key == "properties" else clean(value))
                for key, value in node.items() if key not in ("title", "default")}

    return {"required": [], **clean(full)}
