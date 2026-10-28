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
CHOICES = {"type": "array", "items": {"type": "object", "properties": {"label": STRING, "description": STRING},
                                      "required": ["label"]}}

# What each tool takes, and whether only coordinators get it. What it's for, which the bot reads, is a prompt:
# prompts/tools/<name>.md, which a crew can replace (crewbots.prompts).
SPECS: dict[str, Spec] = {
    "browser_open": Spec(_object({"url": STRING}, ["url"])),
    "browser_screenshot": Spec(_object({"width": {"type": "integer"}, "height": {"type": "integer"},
        "theme": {"type": "string", "enum": ["light", "dark"]}, "full_page": {"type": "boolean"}}, [])),
    "browser_click": Spec(_object({"selector": STRING}, ["selector"])),
    "browser_type": Spec(_object({"selector": STRING, "text": STRING, "key": STRING}, [])),
    "browser_evaluate": Spec(_object({"expression": STRING}, ["expression"])),
    "browser_console": Spec(_object({"clear": {"type": "boolean"}}, [])),
    "handoff": Spec(_object({"done": STRING, "output": STRINGS, "open": STRING, "needs": STRING}, ["done"])),
    "escalate": Spec(_object({"question": STRING, "choices": CHOICES, "multiple": {"type": "boolean"}}, ["question"])),
    "ask_permission": Spec(_object({"what": STRING, "why": STRING, "kind": {"type": "string", "enum": [
        "action", "folder", "network"]}, "folder": STRING}, ["what"])),
    "review": Spec(_object({
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "summary": STRING,
        "findings": {"type": "array", "items": _object(
            {"location": STRING, "problem": STRING, "scenario": STRING}, ["location", "problem"])},
    }, ["verdict", "summary"])),
    "remember": Spec(_object({"title": STRING, "body": STRING, "shared": {"type": "boolean"}}, ["title", "body"])),
    "write_page": Spec(_object({"page": STRING, "text": STRING}, ["page", "text"])),
    "edit_page": Spec(_object({"page": STRING, "find": STRING, "replace": STRING}, ["page", "find", "replace"])),
    "forget": Spec(_object({"name": STRING, "shared": {"type": "boolean"}}, ["name"])),
    "recall": Spec(_object({"query": STRING}, ["query"])),
    "update_state": Spec(_object({"state": STRING}, ["state"])),
    "set_name": Spec(_object({"name": STRING}, ["name"])),
    "remind_on": Spec(_object({"event": STRING, "match": STRING, "then": STRING}, ["event", "then"])),
    "propose_routine": Spec(_object({"name": STRING, "description": STRING, "goal": STRING, "schedule": STRING,
                                     "on": STRING, "repo": STRING}, ["name", "description", "goal"])),
    "start_thread": Spec(_object({"title": STRING, "goal": STRING, "project": STRING}, ["title", "goal"])),
    "post": Spec(_object({"text": STRING, "room": STRING}, ["text"])),
    "share": Spec(_object({"files": STRINGS, "text": STRING, "room": STRING}, ["files"])),
    "ask": Spec(_object({"bot": STRING, "question": STRING}, ["bot", "question"])),
    "open_group": Spec(_object({"title": STRING, "members": STRINGS, "lead": STRING}, ["title", "members"])),
    "add_to_group": Spec(_object({"bot": STRING, "room": STRING}, ["bot"])),
    "add_bot": Spec(_object(
        {"name": STRING, "title": STRING, "description": STRING, "instructions": STRING, "display_name": STRING,
         "backend": {"type": "string", "enum": ["claude", "codex"]}, "model": STRING, "effort": STRING,
         "access": {"type": "string", "enum": ["read_only", "workspace_write"]},
         "network": {"type": "boolean"}, "can_delegate": {"type": "boolean"}, "mcp": STRINGS, "skills": STRINGS},
        ["name", "title", "description", "instructions"]), coordinator_only=True),
    "delegate": Spec(_object({"bot": STRING, "goal": STRING, "project": STRING, "workspace": STRING,
                              "review_of": STRING}, ["bot", "goal"])),
    "tasks": Spec(_object({}, [])),
    "message": Spec(_object({"task_id": STRING, "text": STRING}, ["task_id", "text"])),
    "propose_merge": Spec(_object({"task_id": STRING, "summary": STRING}, ["task_id", "summary"]), coordinator_only=True),
    "withdraw_merge": Spec(_object({"task_id": STRING, "reason": STRING}, ["task_id", "reason"]), coordinator_only=True),
    "next_check": Spec(_object({"minutes": {"type": "number"}, "why": STRING}, ["minutes", "why"]), coordinator_only=True),
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

    async def call(self, name: str, args: dict[str, Any]) -> str | list[dict[str, Any]]:
        """Runs one tool and returns its result for the bot; raises ToolRefused with the reason it can't."""
        from crewbots.attachments import AttachmentError
        from crewbots.browser_cdp import BrowserError
        from crewbots.browser_tools import browser_call
        from crewbots.crew import CrewError
        from crewbots.memory import MemoryError
        from crewbots.pages import PageError
        from crewbots.runner import MergeError, NotAllowed, UnknownBot
        from crewbots.store import Verdict

        tool = SPECS.get(name)
        if tool is None or (tool.coordinator_only and not self.can_delegate):
            raise ToolRefused(f"{name} is not one of this bot's crew tools")
        missing = [k for k in tool.schema["required"] if args.get(k) in (None, "")]
        if missing:
            raise ToolRefused(f"{name} needs {', '.join(missing)}")
        r, me = self.runner, self.task_id
        try:
            if name.startswith("browser_"):
                return await browser_call(r, me, name, args)
            match name:
                case "handoff":
                    r.handoff(me, args["done"], list(args.get("output") or []), args.get("open") or "", args.get("needs") or "")
                    return "Handoff recorded. End your reply with a short summary."
                case "escalate":
                    r.escalate(me, args["question"], args.get("choices"), bool(args.get("multiple")))
                    return ("Asked: they see your question, with its choices if it has any, and can answer in their own "
                            "words. End your turn: the answer arrives as your next message.")
                case "ask_permission":
                    r.ask_permission(me, args["what"], args.get("why") or "", args.get("kind") or "action",
                                     args.get("folder"))
                    return "Asked the user. End your turn now: their answer arrives as your next message."
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
                    child = r.delegate(me, args["bot"], args["goal"], project=args.get("project") or None,
                                       workspace=args.get("workspace") or None,
                                       review_of=args.get("review_of") or None)
                    return f"Delegated {child.id} to {child.bot}: {child.title}"
                case "tasks":
                    import json

                    return json.dumps(r.children_of(me), indent=1, default=str)
                case "message":
                    task = r.message_child(me, args["task_id"], args["text"])
                    return f"Sent to {task.id} ({task.status})."
                case "propose_merge":
                    task = r.propose_merge(me, args["task_id"], args["summary"])
                    return (f"Merged {task.id} locally as {task.merged_commit}." if task.merge_state == "merged"
                            else f"Proposed {task.id} to the user for merging.")
                case "withdraw_merge":
                    task = r.withdraw_merge(args["task_id"], args["reason"], parent_id=me)
                    return f"Withdrew the merge proposal for {task.id}."
                case "remember":
                    note = r.remember(me, args["title"], args["body"], shared=bool(args.get("shared")))
                    return f"Remembered: {note.title} ({note.path})."
                case "next_check":
                    at = r.next_check(me, args["minutes"], args["why"])
                    return f"You'll look around again at {at:%H:%M} ({at:%a %d %b}), unless something comes up first."
                case "write_page":
                    page = r.write_page(me, args["page"], args["text"])
                    return f"Wrote {page.name} ({page.path})."
                case "edit_page":
                    page = r.write_page(me, args["page"], args["replace"], find=args["find"])
                    return f"Changed {page.name}."
                case "forget":
                    note = r.forget(me, args["name"], shared=bool(args.get("shared")))
                    return f"Forgot: {note.title}."
                case "recall":
                    return recall_text(r.recall(me, args["query"]))
                case "update_state":
                    return f"Kept this task's state ({r.update_state(me, args['state'])})."
                case "set_name":
                    bot = r.set_name(me, args["name"])
                    return f"You go by {bot.label} now: the app shows it for you. Your handle, @{bot.name}, stays."
                case "propose_routine":
                    r.propose_routine(me, args["name"], args["description"], args["goal"], args.get("schedule") or None,
                                      args.get("on") or None, args.get("repo") or None)
                    return (f"Proposed the routine {args['name']!r}. It's off until the user turns it on in the app "
                            "(Routines), and its first run is a test they watch: tell them it's waiting there.")
                case "remind_on":
                    intent = r.remind_on(me, args["event"], args.get("match") or "", args["then"])
                    return f"Standing intent {intent['id']} set: on {intent['kind']} {intent['match']}".rstrip() + "."
                case "add_bot":
                    added = r.add_bot(me, args)
                    return (f"Added {added.label} (@{added.name}), {added.title}, to the crew. It's in the roster now: "
                            "@mention it in a group chat to bring it in.")
                case "start_thread":
                    thread = r.start_thread(me, args["title"], args["goal"], args.get("project") or None)
                    return f"Opened thread {thread.id}: {thread.title}. Its result comes back here when it's done."
                case "ask":
                    asked = r.ask(me, args["bot"], args["question"])
                    return f"Asked {asked.bot} ({asked.id}). End your turn: the answer arrives as your next message."
                case "open_group":
                    room = r.open_group(me, args["title"], list(args["members"]), args.get("lead") or None)
                    return (f"Opened the group chat {room['title']!r} ({room['id']}) with {', '.join(room['members'])}, "
                            f"led by {room['lead']}. Say something there with post(text, room={room['id']!r}).")
                case "add_to_group":
                    room = r.add_to_group(me, args["bot"], args.get("room") or None)
                    return f"{args['bot']} is in {room['title']!r} now: @mention them there to bring them in."
                case "post":
                    message = r.post_from(me, args["text"], args.get("room") or None)
                    return f"Posted in the group chat ({message['room_id']})."
                case "share":
                    files = [args["files"]] if isinstance(args["files"], str) else [str(f) for f in args["files"]]
                    return shared_text(await r.share(me, files, args.get("text") or "", args.get("room") or None))
        except NotAllowed as e:  # logged with what bots do to each other, as the API logs it
            self.runner.store.log_agent("refused", self.runner.store.get_task(self.task_id).bot, task_id=self.task_id,
                                        text=f"{name}: {e}")
            raise ToolRefused(str(e)) from None
        except (UnknownBot, MemoryError, CrewError, AttachmentError, PageError, BrowserError, MergeError) as e:
            raise ToolRefused(str(e.args[0] if e.args else e)) from None
        raise ToolRefused(f"{name} is not implemented")


def shared_text(shared: dict[str, Any]) -> str:
    names = ", ".join(f["name"] for f in shared["files"])
    where = f"in the group chat ({shared['room_id']})" if shared.get("room_id") else "with the user, here"
    them = "it" if len(shared["files"]) == 1 else "them"
    return f"Shared {names} {where}. They see {them} under your message; no need to repeat the path."


def recall_text(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "Nothing in the crew's record matches that."
    lines = [f"- {h['task_id']} ({h['bot']}, {h['at'][:10]}, {h['kind']}) {h['title']}: {h['snippet']}" if h["task_id"]
             else f"- {h['kind']} ({h['bot']}{', ' + h['at'] if h['at'] else ''}) {h['title']}: {h['snippet']}" for h in hits]
    return "\n".join(lines)
