"""The crew tools, defined once: what a bot can do in the crew, for one task.

A backend that can host tools in-process (Claude, through the Agent SDK) calls `CrewTools` directly, with no server
and no token. One that can't (Codex) gets the same tools from `crewbots.mcp_server`, which reaches the daemon's /bot/
API with a session token. Either way the runner decides what the task may do.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crewbots import prompts

if TYPE_CHECKING:
    from crewbots.runner import Runner


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]  # JSON schema of the arguments
    coordinator_only: bool = False


@dataclass(frozen=True)
class Spec:
    schema: dict[str, Any]  # JSON schema of the arguments
    coordinator_only: bool = False


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": {"type": "string"}}

# What each tool takes, and whether only coordinators get it. What it's for, which the bot reads, is a prompt:
# prompts/tools/<name>.md, which a crew can replace (crewbots.prompts).
SPECS: dict[str, Spec] = {
    "handoff": Spec(_object({"done": STRING, "output": STRINGS, "open": STRING, "needs": STRING}, ["done"])),
    "escalate": Spec(_object({"question": STRING}, ["question"])),
    "review": Spec(_object({
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "summary": STRING,
        "findings": {"type": "array", "items": _object(
            {"location": STRING, "problem": STRING, "scenario": STRING}, ["location", "problem"])},
    }, ["verdict", "summary"])),
    "remember": Spec(_object({"title": STRING, "body": STRING, "shared": {"type": "boolean"}}, ["title", "body"])),
    "forget": Spec(_object({"name": STRING, "shared": {"type": "boolean"}}, ["name"])),
    "recall": Spec(_object({"query": STRING}, ["query"])),
    "start_thread": Spec(_object({"title": STRING, "goal": STRING}, ["title", "goal"])),
    "post": Spec(_object({"text": STRING, "room": STRING}, ["text"])),
    "add_bot": Spec(_object(
        {"name": STRING, "title": STRING, "description": STRING, "instructions": STRING, "display_name": STRING,
         "backend": {"type": "string", "enum": ["claude", "codex"]}, "model": STRING, "effort": STRING,
         "access": {"type": "string", "enum": ["read_only", "workspace_write"]},
         "network": {"type": "boolean"}, "can_delegate": {"type": "boolean"}, "mcp": STRINGS, "skills": STRINGS},
        ["name", "title", "description", "instructions"]), coordinator_only=True),
    "delegate": Spec(_object({"bot": STRING, "goal": STRING, "workspace": STRING, "review_of": STRING}, ["bot", "goal"])),
    "tasks": Spec(_object({}, [])),
    "message": Spec(_object({"task_id": STRING, "text": STRING}, ["task_id", "text"])),
    "propose_merge": Spec(_object({"task_id": STRING, "summary": STRING}, ["task_id", "summary"]), coordinator_only=True),
}


def describe(name: str, home: Path | None = None) -> str:
    """What the crew tool `name` is for, as the bot reads it."""
    return prompts.text(f"tools/{name}", home)


def tools_for(can_delegate: bool, home: Path | None = None) -> list[ToolDef]:
    return [ToolDef(name, describe(name, home), spec.schema, spec.coordinator_only) for name, spec in SPECS.items()
            if can_delegate or not spec.coordinator_only]


class ToolRefused(Exception):
    """A tool call the crew's rules don't allow, or that was malformed. The message goes back to the bot."""


class CrewTools:
    """The crew tools for one task, called in the daemon's own process."""

    def __init__(self, runner: "Runner", task_id: str, can_delegate: bool):
        self.runner = runner
        self.task_id = task_id
        self.can_delegate = can_delegate

    def definitions(self) -> list[ToolDef]:
        return tools_for(self.can_delegate, self.runner.crew.home)

    async def call(self, name: str, args: dict[str, Any]) -> str:
        """Runs one tool and returns its result for the bot; raises ToolRefused with the reason it can't."""
        from crewbots.crew import CrewError
        from crewbots.memory import MemoryError
        from crewbots.runner import NotAllowed, UnknownBot
        from crewbots.store import Verdict

        tool = SPECS.get(name)
        if tool is None or (tool.coordinator_only and not self.can_delegate):
            raise ToolRefused(f"{name} is not one of this bot's crew tools")
        missing = [k for k in tool.schema["required"] if args.get(k) in (None, "")]
        if missing:
            raise ToolRefused(f"{name} needs {', '.join(missing)}")
        r, me = self.runner, self.task_id
        try:
            match name:
                case "handoff":
                    r.handoff(me, args["done"], list(args.get("output") or []), args.get("open") or "", args.get("needs") or "")
                    return "Handoff recorded. End your reply with a short summary."
                case "escalate":
                    r.escalate(me, args["question"])
                    return "Escalated. End your turn now; the answer will arrive as your next message."
                case "review":
                    try:
                        verdict = Verdict(str(args["verdict"]).lower())
                    except ValueError:
                        raise ToolRefused("verdict must be pass or fail") from None
                    findings = [{k: str(f.get(k, "")) for k in ("location", "problem", "scenario")}
                                for f in args.get("findings") or [] if isinstance(f, dict)]
                    r.review(me, verdict, args["summary"], findings)
                    return "Verdict recorded. End your reply with a short summary."
                case "delegate":
                    child = r.delegate(me, args["bot"], args["goal"], workspace=args.get("workspace") or None,
                                       review_of=args.get("review_of") or None)
                    return f"Delegated {child.id} to {child.bot}: {child.title}"
                case "tasks":
                    import json

                    return json.dumps(r.children_of(me), indent=1, default=str)
                case "message":
                    task = r.message_child(me, args["task_id"], args["text"])
                    return f"Sent to {task.id} ({task.status})."
                case "propose_merge":
                    r.propose_merge(me, args["task_id"], args["summary"])
                    return f"Proposed {args['task_id']} to the user for merging."
                case "remember":
                    note = r.remember(me, args["title"], args["body"], shared=bool(args.get("shared")))
                    return f"Remembered: {note.title} ({note.path})."
                case "forget":
                    note = r.forget(me, args["name"], shared=bool(args.get("shared")))
                    return f"Forgot: {note.title}."
                case "recall":
                    return recall_text(r.recall(me, args["query"]))
                case "add_bot":
                    added = r.add_bot(me, args)
                    return (f"Added {added.label} (@{added.name}), {added.title}, to the crew. It's in the roster now: "
                            "@mention it in a group chat to bring it in.")
                case "start_thread":
                    thread = r.start_thread(me, args["title"], args["goal"])
                    return f"Opened thread {thread.id}: {thread.title}. Its result comes back here when it's done."
                case "post":
                    message = r.post_from(me, args["text"], args.get("room") or None)
                    return f"Posted in the group chat ({message['room_id']})."
        except (NotAllowed, UnknownBot, MemoryError, CrewError) as e:
            raise ToolRefused(str(e.args[0] if e.args else e)) from None
        raise ToolRefused(f"{name} is not implemented")


def recall_text(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "Nothing in the crew's record matches that."
    lines = [f"- {h['task_id']} ({h['bot']}, {h['at'][:10]}, {h['kind']}) {h['title']}: {h['snippet']}" for h in hits]
    return "\n".join(lines)
