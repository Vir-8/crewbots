"""The crew tools, defined once: what a bot can do in the crew, for one task.

A backend that can host tools in-process (Claude, through the Agent SDK) calls `CrewTools` directly, with no server
and no token. One that can't (Codex) gets the same tools from `crewbots.mcp_server`, which reaches the daemon's /bot/
API with a session token. Either way the runner decides what the task may do.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crewbots import prompts, tool_args

if TYPE_CHECKING:
    from crewbots.runner import Runner


def merge_text(task_id: str, state: str | None, commit: str | None, details: dict[str, Any]) -> str:
    if state != "merged":
        return f"Proposed {task_id} to the user for merging."
    message = f"Merged {task_id} locally as {commit}."
    if ignored := details.get("ignored"):
        message += (" Left out, as git ignores them in this project: " + ", ".join(ignored)
                    + ". Whether to track them is the user's call: say so in your report.")
    return message


def asked_text(for_task: str | None) -> str:
    if for_task:
        return f"Asked the user, for {for_task}: their answer goes to it, which carries on from there."
    return "Asked the user. End your turn now: their answer arrives as your next message."


def told_text(item: dict[str, Any]) -> str:
    return ("Told the user in your chat with them" + (", with a notification." if item["level"] == "needs_you" else
                                                       ". It's there for when they look; no notification now."))


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    schema: dict[str, Any]  # JSON schema of the arguments


@dataclass(frozen=True)
class Spec:
    args: type[tool_args.Args]  # what it takes
    coordinator_only: bool = False

    @property
    def schema(self) -> dict[str, Any]:  # JSON schema of the arguments
        return tool_args.schema(self.args)


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


STRING = {"type": "string"}

# What each tool takes, and whether only coordinators get it. What it's for, which the bot reads, is a prompt:
# prompts/tools/<name>.md, which a crew can replace (crewbots.prompts).
SPECS: dict[str, Spec] = {
    "browser_open": Spec(tool_args.BrowserOpen),
    "browser_screenshot": Spec(tool_args.BrowserScreenshot),
    "browser_click": Spec(tool_args.BrowserClick),
    "browser_type": Spec(tool_args.BrowserType),
    "browser_evaluate": Spec(tool_args.BrowserEvaluate),
    "browser_console": Spec(tool_args.BrowserConsole),
    "webkit_check": Spec(tool_args.WebKitCheck),
    "handoff": Spec(tool_args.HandoffBody),
    "escalate": Spec(tool_args.Escalation),
    "ask_permission": Spec(tool_args.PermissionAsk),
    "review": Spec(tool_args.ReviewBody),
    "remember": Spec(tool_args.Remember),
    "write_page": Spec(tool_args.PageWrite),
    "edit_page": Spec(tool_args.PageEdit),
    "forget": Spec(tool_args.Forget),
    "recall": Spec(tool_args.Recall),
    "working_state": Spec(tool_args.WorkingStateEdit),
    "update_state": Spec(tool_args.StateBody),
    "set_name": Spec(tool_args.NameBody),
    "remind_on": Spec(tool_args.RemindOn),
    "propose_routine": Spec(tool_args.ProposedRoutine),
    "start_thread": Spec(tool_args.ThreadBody),
    "post": Spec(tool_args.PostBody),
    "tell_user": Spec(tool_args.TellBody),
    "share": Spec(tool_args.ShareBody),
    "ask": Spec(tool_args.AskBody),
    "open_group": Spec(tool_args.OpenGroup),
    "add_to_group": Spec(tool_args.GroupMember),
    "add_bot": Spec(tool_args.AddBot, coordinator_only=True),
    "delegate": Spec(tool_args.Delegation),
    "tasks": Spec(tool_args.NoArgs),
    "room_status": Spec(tool_args.RoomStatus, coordinator_only=True),
    "message": Spec(tool_args.ChildMessage),
    "stop_task": Spec(tool_args.TaskStop),
    "propose_push": Spec(tool_args.PushProposal, coordinator_only=True),
    "propose_replace_main": Spec(tool_args.MainReplacement, coordinator_only=True),
    "propose_merge": Spec(tool_args.MergeProposal, coordinator_only=True),
    "withdraw_merge": Spec(tool_args.MergeWithdrawal, coordinator_only=True),
    "sweep_worktrees": Spec(tool_args.WorktreeSweep, coordinator_only=True),
    "drop_task": Spec(tool_args.TaskDrop, coordinator_only=True),
    "next_check": Spec(tool_args.NextCheck, coordinator_only=True),
    "open_unity_editor": Spec(tool_args.NoArgs),
    "release_app": Spec(tool_args.NoArgs),
}


CHIEF_ONLY = {"add_bot", "propose_push", "propose_replace_main", "tell_user"}
# Tools only some tasks are offered, by what the task is: listed to every task, they'd cost each turn for nothing.
# The daemon still refuses one used out of place, with why.
WHERE_THE_USER_TALKS = {"propose_routine", "remind_on"}  # a chat or a group chat
NOT_IN_REVIEWS = {"delegate", "start_thread", "open_group", "add_to_group", "write_page", "edit_page", "handoff"}


def offered(name: str, kind: str | None, reviewing: bool) -> bool:
    """Whether a task of this kind (None: any) is offered the tool `name`."""
    if kind is None:
        return True
    if name == "review":
        return reviewing
    if reviewing and (name in NOT_IN_REVIEWS or name in WHERE_THE_USER_TALKS):
        return False
    if name == "handoff":
        return kind not in ("chat", "seat")  # nothing to hand back: a conversation goes on
    if name == "set_name":
        return kind == "chat"
    if name == "tell_user":
        return kind != "chat"  # in its chat with the user, what it says there already is
    if name == "open_unity_editor":
        return kind not in ("chat", "seat", "ask")  # work in a worktree of its own
    return name not in WHERE_THE_USER_TALKS or kind in ("chat", "seat")


def describe(name: str, home: Path | None = None) -> str:
    """What the crew tool `name` is for, as the bot reads it."""
    return prompts.text(f"tools/{name}", home)


def tools_for(can_delegate: bool, home: Path | None = None, *, is_chief: bool = False, kind: str | None = None,
              reviewing: bool = False) -> list[ToolDef]:
    return [ToolDef(name, describe(name, home), spec.schema) for name, spec in SPECS.items()
            if (can_delegate or not spec.coordinator_only) and (name not in CHIEF_ONLY or is_chief)
            and offered(name, kind, reviewing)]


class ToolRefused(Exception):
    """A tool call the crew's rules don't allow, or that was malformed. The message goes back to the bot."""


class CrewTools:
    """The crew tools for one task, called in the daemon's own process."""

    def __init__(self, runner: "Runner", task_id: str, can_delegate: bool, *, follows: str | None = None):
        self.runner = runner
        self._task_id = task_id
        self.can_delegate = can_delegate
        self.follows = follows  # a session token: a session shared by a chat and its seats acts for whichever has the turn

    @property
    def task_id(self) -> str:
        return self.runner._tokens.get(self.follows, self._task_id) if self.follows else self._task_id

    def definitions(self) -> list[ToolDef]:
        task = self.runner.store.get_task(self.task_id)
        where = {"kind": task.kind, "reviewing": bool(task.review_of)}
        if not self.can_delegate:
            return tools_for(False, self.runner.crew.home, **where)
        return tools_for(self.runner.is_coordinator(task.bot), self.runner.crew.home,
                         is_chief=self.runner.is_chief(task.bot), **where)

    async def call(self, name: str, given: dict[str, Any]) -> str | list[dict[str, Any]]:
        """Runs one tool and returns its result for the bot; raises ToolRefused with the reason it can't."""
        tool = SPECS.get(name)
        bot = self.runner.store.get_task(self.task_id).bot
        if (tool is None or (tool.coordinator_only and
                             not (self.can_delegate and self.runner.is_coordinator(bot))) or
                (name in CHIEF_ONLY and not (self.can_delegate and self.runner.is_chief(bot)))):
            if name in {"propose_push", "propose_replace_main"}:
                raise ToolRefused(f"Only the chief can use {name}")
            raise ToolRefused(f"{name} is not one of this bot's crew tools")
        try:
            args, unknown = tool_args.read(name, tool.args, given)
        except tool_args.ArgsError as e:
            raise ToolRefused(str(e)) from None
        result = await self._run(name, args)
        if unknown and isinstance(result, str):  # said, so it isn't taken as done when what it meant was dropped
            result += "\n\n" + tool_args.left_out(name, tool.args, unknown)
        return result

    async def _run(self, name: str, args: dict[str, Any]) -> str | list[dict[str, Any]]:
        from crewbots.attachments import AttachmentError
        from crewbots.browser_cdp import BrowserError
        from crewbots.browser_tools import browser_call, webkit_call
        from crewbots.crew import CrewError
        from crewbots.memory import MemoryError
        from crewbots.pages import PageError
        from crewbots.runner import MergeError, NotAllowed, UnknownBot
        from crewbots.store import Verdict
        from crewbots.webkit import WebKitError

        r, me = self.runner, self.task_id
        try:
            if name.startswith("browser_"):
                return await browser_call(r, me, name, args)
            if name == "webkit_check":
                return await webkit_call(r, me, args)
            match name:
                case "handoff":
                    r.handoff(me, args["done"], args["output"], args["open"], args["needs"], args["evidence"],
                              args["checks"])
                    wrong = r.wrong_branch(r.store.get_task(me))
                    return "Handoff recorded. End your reply with a short summary." + (f"\n\n{wrong}" if wrong else "")
                case "escalate":
                    r.escalate(me, args["question"], args["choices"], args["multiple"])
                    return ("Asked: they see your question, with its choices if it has any, and can answer in their own "
                            "words. End your turn: the answer arrives as your next message.")
                case "ask_permission":
                    r.ask_permission(me, args["what"], args["why"], args["kind"], args["folder"], args["task_id"] or None)
                    return asked_text(args["task_id"])
                case "review":
                    r.review(me, Verdict(args["verdict"]), args["summary"], args["findings"])
                    return "Verdict recorded. End your reply with a short summary."
                case "delegate":
                    child = r.delegate(me, args["bot"], args["goal"], project=args["project"] or None,
                                       workspace=args["workspace"] or None, review_of=args["review_of"] or None)
                    # The app finds the child's card by its id in this reply, and in start_thread's and ask's.
                    return f"Delegated {child.id} to {child.bot}: {child.title}"
                case "tasks":
                    import json

                    return json.dumps(r.delegated_by(me), indent=1, default=str)
                case "room_status":
                    import json

                    return json.dumps(r.room_status(me, args["room"] or None), indent=1)
                case "sweep_worktrees":
                    import json

                    return json.dumps(await r.bot_worktree_sweep(me, args["dry_run"]), indent=1)
                case "message":
                    task = r.message_child(me, args["task_id"], args["text"])
                    return f"Sent to {task.id} ({task.status})."
                case "stop_task":
                    task = await r.stop_work(args["task_id"], by_task=me)
                    return f"{task.id} is {task.status}."
                case "propose_push":
                    from crewbots.push import proposal_message, propose
                    proposal = await propose(r, me, args["project"], args["summary"])
                    return proposal_message(r, proposal)
                case "propose_replace_main":
                    from crewbots.replace_main import proposal_message, propose
                    proposal = await propose(r, me, args["project"], args["branch"], args["summary"])
                    return proposal_message(r, proposal)
                case "propose_merge":
                    task = await r.propose_merge_off_loop(me, args["task_id"], args["summary"])
                    return merge_text(task.id, task.merge_state, task.merged_commit, r.merged_details(task))
                case "withdraw_merge":
                    task = r.withdraw_merge(args["task_id"], args["reason"], parent_id=me)
                    return f"Withdrew the merge proposal for {task.id}."
                case "drop_task":
                    return dropped_text(await r.drop(args["task_id"], args["reason"], by_task=me))
                case "working_state":
                    from crewbots import working_state
                    path = working_state.path(r.crew.memory.root, r.store.get_task(me).bot)
                    action, entry_id = args["action"], args["entry_id"]
                    if action == "list":  # a look: what's there, with each entry's id
                        if section := args["section"]:
                            return "\n".join(working_state.parse(path).get(section, [])) or f"Nothing in {section}."
                        return working_state.read(path)
                    if not (section := args["section"] or working_state.section_of(path, entry_id)):
                        raise ToolRefused(f"no entry {entry_id!r} in your working state" if entry_id
                                          else "say which section to change")
                    entry_id, pruned = working_state.edit(path, section, action, args["text"], entry_id, args["done"])
                    if section == "Needs me" and action == "add" and not args["done"]:
                        r.need_added(me, pruned)
                    made = {"add": "added", "replace": "replaced", "remove": "removed"}[action]
                    return (f"Working state entry {entry_id} {made}."
                            + (" State entries were trimmed; tidy up your state." if pruned else ""))
                case "remember":
                    earlier = r.corrections_of(r.store.get_task(me).bot) if args["mistake"].strip() else None
                    note = r.remember(me, args["title"], args["body"], shared=args["shared"], mistake=args["mistake"],
                                      check=args["check"])
                    return remembered_text({"title": note.title, "path": str(note.path), "earlier": earlier})
                case "open_unity_editor":
                    return r.open_unity_editor(me)
                case "release_app":
                    return r.release_app(me)
                case "next_check":
                    at = r.next_check(me, args["minutes"], args["why"])
                    return f"You'll look around again at {at:%H:%M} ({at:%a %d %b}), unless something comes up first."
                case "write_page":
                    page = r.write_page(me, args["page"], args["content"])
                    return f"Wrote {page.name} ({page.path})."
                case "edit_page":
                    page = r.write_page(me, args["page"], args["replace"], find=args["find"])
                    return f"Changed {page.name}."
                case "forget":
                    note = r.forget(me, args["name"], shared=args["shared"])
                    return f"Forgot: {note.title}."
                case "recall":
                    return recall_text(r.recall(me, args["query"]))
                case "update_state":
                    return f"Kept this task's state ({r.update_state(me, args['state'])})."
                case "set_name":
                    bot = r.set_name(me, args["name"])
                    return f"You go by {bot.label} now: the app shows it for you. Your handle, @{bot.name}, stays."
                case "propose_routine":
                    r.propose_routine(me, args["name"], args["description"], args["goal"], args["schedule"] or None,
                                      args["on"] or None, args["repo"] or None)
                    return (f"Proposed the routine {args['name']!r}. It's off until the user turns it on in the app "
                            "(Routines), and its first run is a test they watch: tell them it's waiting there.")
                case "remind_on":
                    intent = r.remind_on(me, args["event"], args["match"], args["then"])
                    return f"Standing intent {intent['id']} set: on {intent['kind']} {intent['match']}".rstrip() + "."
                case "add_bot":
                    try:
                        added = r.add_bot(me, args)
                    except (KeyError, ValueError) as e:  # a missing field, or an access that isn't one
                        raise ToolRefused(f"add_bot: {e}") from None
                    return (f"Added {added.label} (@{added.name}), {added.title}, to the crew. It's in the roster now: "
                            "@mention it in a group chat to bring it in.")
                case "start_thread":
                    thread = r.start_thread(me, args["title"], args["goal"], args["project"] or None)
                    return f"Opened thread {thread.id}: {thread.title}. Its result comes back here when it's done."
                case "ask":
                    asked = r.ask(me, args["bot"], args["question"])
                    return f"Asked {asked.bot} ({asked.id}). End your turn: the answer arrives as your next message."
                case "open_group":
                    room = r.open_group(me, args["title"], args["members"], args["lead"] or None)
                    return (f"Opened the group chat {room['title']!r} ({room['id']}) with {', '.join(room['members'])}, "
                            f"led by {room['lead']}. Say something there with post(text, room={room['id']!r}).")
                case "add_to_group":
                    room = r.add_to_group(me, args["bot"], args["room"] or None)
                    return f"{args['bot']} is in {room['title']!r} now: @mention them there to bring them in."
                case "post":
                    message = r.post_from(me, args["text"], args["room"] or None)
                    return f"Posted in the group chat ({message['room_id']})."
                case "tell_user":
                    return told_text(r.tell_user(me, args["text"], args["needs_you"]))
                case "share":
                    return shared_text(await r.share(me, args["files"], args["text"], args["room"] or None))
        except NotAllowed as e:  # logged with what bots do to each other, as the API logs it
            self.runner.store.log_agent("refused", self.runner.store.get_task(self.task_id).bot, task_id=self.task_id,
                                        text=f"{name}: {e}")
            raise ToolRefused(str(e)) from None
        except (UnknownBot, MemoryError, CrewError, AttachmentError, PageError, BrowserError, WebKitError, MergeError) as e:
            raise ToolRefused(str(e.args[0] if e.args else e)) from None
        raise ToolRefused(f"{name} is not implemented")


def remembered_text(note: dict[str, Any]) -> str:
    text = f"Remembered: {note['title']} ({note['path']})."
    if note.get("earlier") is None:
        return text
    text += " Counted as a correction: the weekly retro turns mistakes that come back into checks."
    if note["earlier"]:
        text += " Your earlier ones, the latest first: " + "; ".join(note["earlier"]) + "."
    return text


def dropped_text(result: dict[str, str]) -> str:
    if result["status"] == "already dropped":
        return f"{result['task_id']} was already dropped."
    return f"Dropped {result['task_id']}: {result['reason']}. It's closed, and no longer counts as open work."


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
