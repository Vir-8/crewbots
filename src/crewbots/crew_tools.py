"""The crew tools, defined once: what a bot can do in the crew, for one task.

A backend that can host tools in-process (Claude, through the Agent SDK) calls `CrewTools` directly, with no server
and no token. One that can't (Codex) gets the same tools from `crewbots.mcp_server`, which reaches the daemon's /bot/
API with a session token. Either way the runner decides what the task may do.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from crewbots.runner import Runner


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]  # JSON schema of the arguments
    coordinator_only: bool = False


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": {"type": "string"}}

TOOLS = [
    ToolDef(
        "handoff",
        "Hand your finished work back, as the HANDOFF in team/protocol.md. Call it once, when you are done. "
        "done: what you completed. output: file paths, branches or links to the output. open: anything unfinished or "
        "uncertain. needs: what the next step needs, from whoever picks this up.",
        _object({"done": STRING, "output": STRINGS, "open": STRING, "needs": STRING}, ["done"]),
    ),
    ToolDef(
        "escalate",
        "Ask for a decision that is not yours to make (team/rules.md). Ask one clear question, then end your turn: "
        "the answer arrives as your next message.",
        _object({"question": STRING}, ["question"]),
    ),
    ToolDef(
        "review",
        "Give your verdict, when your task is a review. The verdict is what counts, not your prose. verdict: pass or "
        "fail. summary: one or two sentences. findings: one per problem, each with location (file:line), problem, and "
        "scenario (the input or state, and the wrong result). A pass with remaining risks is still a pass; list the "
        "risks as findings.",
        _object({
            "verdict": {"type": "string", "enum": ["pass", "fail"]},
            "summary": STRING,
            "findings": {"type": "array", "items": _object(
                {"location": STRING, "problem": STRING, "scenario": STRING}, ["location", "problem"])},
        }, ["verdict", "summary"]),
    ),
    ToolDef(
        "remember",
        "Keep a note for next time: something that will still be true and useful in later tasks (what the user "
        "prefers, a decision and why, a pitfall here, how something is done). One fact per note. title: short, names "
        "the fact. body: the fact, and why it matters. shared: true to keep it for the whole crew, not just you. The "
        "same title again updates the note.",
        _object({"title": STRING, "body": STRING, "shared": {"type": "boolean"}}, ["title", "body"]),
    ),
    ToolDef(
        "forget",
        "Remove a note that's wrong or out of date. name: the note's title or file name. shared: true for a crew note.",
        _object({"name": STRING, "shared": {"type": "boolean"}}, ["name"]),
    ),
    ToolDef(
        "recall",
        "Search everything the crew has said and decided (messages, replies, handoffs, reviews, questions) in earlier "
        "tasks. query: a few words. Results are the best matches, with task ids, bots and dates.",
        _object({"query": STRING}, ["query"]),
    ),
    ToolDef(
        "start_thread",
        "Open a thread for a piece of real work (several steps, files, anything that takes a while), so the "
        "conversation you're in stays a conversation. You work the thread yourself, in parallel. title: a few words. "
        "goal: the outcome, the context, and what done means; it must stand alone. Its result comes back here.",
        _object({"title": STRING, "goal": STRING}, ["title", "goal"]),
    ),
    ToolDef(
        "post",
        "Say something in a group chat: by default the crew's own, with every bot in it, or the one you're in. "
        "@mention a teammate by name to bring them in; that's how you ask another bot for something, and their reply "
        "comes in the same chat. room: a group chat's id, if not the default.",
        _object({"text": STRING, "room": STRING}, ["text"]),
    ),
    ToolDef(
        "delegate",
        "Give one task to one bot in the crew. It starts at once and runs in parallel with other delegated tasks. "
        "goal: the outcome, the context the bot needs, and what done means; it must stand alone. workspace: a folder "
        "or repo to work in, if not this task's. review_of: the id of a task whose work this one reviews; the reviewer "
        "then works in that task's worktree. After delegating, end your turn: results arrive as your next message once "
        "none of your tasks are running.",
        _object({"bot": STRING, "goal": STRING, "workspace": STRING, "review_of": STRING}, ["bot", "goal"]),
        coordinator_only=True,
    ),
    ToolDef(
        "tasks",
        "The tasks you delegated: status, worktree, handoff and final message for each. For looking something up, "
        "never for waiting: results come to you.",
        _object({}, []),
        coordinator_only=True,
    ),
    ToolDef(
        "message",
        "Send a message to a task you delegated: answer its escalation, or send it review objections to fix. Its next "
        "result comes back to you like the first one.",
        _object({"task_id": STRING, "text": STRING}, ["task_id", "text"]),
        coordinator_only=True,
    ),
    ToolDef(
        "propose_merge",
        "Put approved work in front of the user to merge. Every review of its current version must have passed. You "
        "never merge; the user does. Uncommitted changes in the worktree are expected: crewbots commits them when the "
        "user merges. summary: what the change does and how it was verified, for the user deciding whether to merge.",
        _object({"task_id": STRING, "summary": STRING}, ["task_id", "summary"]),
        coordinator_only=True,
    ),
]

BY_NAME = {t.name: t for t in TOOLS}


def tools_for(can_delegate: bool) -> list[ToolDef]:
    return [t for t in TOOLS if can_delegate or not t.coordinator_only]


class ToolRefused(Exception):
    """A tool call the crew's rules don't allow, or that was malformed. The message goes back to the bot."""


class CrewTools:
    """The crew tools for one task, called in the daemon's own process."""

    def __init__(self, runner: "Runner", task_id: str, can_delegate: bool):
        self.runner = runner
        self.task_id = task_id
        self.can_delegate = can_delegate

    def definitions(self) -> list[ToolDef]:
        return tools_for(self.can_delegate)

    async def call(self, name: str, args: dict[str, Any]) -> str:
        """Runs one tool and returns its result for the bot; raises ToolRefused with the reason it can't."""
        from crewbots.memory import MemoryError
        from crewbots.runner import NotAllowed, UnknownBot
        from crewbots.store import Verdict

        tool = BY_NAME.get(name)
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
                case "start_thread":
                    thread = r.start_thread(me, args["title"], args["goal"])
                    return f"Opened thread {thread.id}: {thread.title}. Its result comes back here when it's done."
                case "post":
                    message = r.post_from(me, args["text"], args.get("room") or None)
                    return f"Posted in the group chat ({message['room_id']})."
        except (NotAllowed, UnknownBot, MemoryError) as e:
            raise ToolRefused(str(e.args[0] if e.args else e)) from None
        raise ToolRefused(f"{name} is not implemented")


def recall_text(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "Nothing in the crew's record matches that."
    lines = [f"- {h['task_id']} ({h['bot']}, {h['at'][:10]}, {h['kind']}) {h['title']}: {h['snippet']}" for h in hits]
    return "\n".join(lines)
