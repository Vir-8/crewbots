"""The crewbots MCP server: the crew tools (crewbots.crew_tools) for backends that can't host them in-process, which
is Codex. The CLI starts one per session, over stdio.

It holds nothing but a run token (CREWBOTS_TOKEN) and the daemon's address (CREWBOTS_URL). Every tool is a call to the
daemon's /bot/ API, which decides what this task may do; this process only translates. Coordinators also get the
tools for adding bots and proposing merges (CREWBOTS_DELEGATE=1), which the daemon refuses anyone else regardless.
"""

import json
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, TextContent

from crewbots.crew_tools import describe, recall_text, shared_text

INSTRUCTIONS = "Tools for working in a crewbots crew: hand work back, escalate decisions, and (for coordinators) delegate."


class CrewError(ToolError):
    """The daemon refused or failed. A ToolError, so the bot sees the reason; any other exception reaches the model
    only as "Error executing tool"."""


def build_server(client: httpx.Client, can_delegate: bool) -> MCPServer:
    server = MCPServer(name="crewbots", instructions=INSTRUCTIONS)

    def call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        response = client.request(method, path, json=body)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise CrewError(f"crewbots refused this ({response.status_code}): {detail}")
        return response.json()

    def browser(action, args):
        result = call("POST", f"/bot/browser/{action}", args)
        return [ImageContent(**item) if item["type"] == "image" else TextContent(**item) for item in result["content"]]

    @server.tool(description=describe("browser_open"))
    def browser_open(url: str) -> list[TextContent | ImageContent]:
        return browser("open", {"url": url})

    @server.tool(description=describe("browser_screenshot"))
    def browser_screenshot(width: int = 1400, height: int = 860, theme: str = "light",
                           full_page: bool = False) -> list[TextContent | ImageContent]:
        return browser("screenshot", {"width": width, "height": height, "theme": theme, "full_page": full_page})

    @server.tool(description=describe("browser_click"))
    def browser_click(selector: str) -> list[TextContent | ImageContent]:
        return browser("click", {"selector": selector})

    @server.tool(description=describe("browser_type"))
    def browser_type(selector: str | None = None, text: str | None = None,
                     key: str | None = None) -> list[TextContent | ImageContent]:
        return browser("type", {k: v for k, v in {"selector": selector, "text": text, "key": key}.items() if v is not None})

    @server.tool(description=describe("browser_evaluate"))
    def browser_evaluate(expression: str) -> list[TextContent | ImageContent]:
        return browser("evaluate", {"expression": expression})

    @server.tool(description=describe("browser_console"))
    def browser_console(clear: bool = False) -> list[TextContent | ImageContent]:
        return browser("console", {"clear": clear})

    @server.tool(description=describe("handoff"))
    def handoff(done: str, output: list[str] | None = None, open: str = "", needs: str = "") -> str:
        call("POST", "/bot/handoff", {"done": done, "output": output or [], "open": open, "needs": needs})
        return "Handoff recorded. End your reply with a short summary."

    @server.tool(description=describe("escalate"))
    def escalate(question: str, choices: list[dict[str, str]] | None = None, multiple: bool = False) -> str:
        call("POST", "/bot/escalate", {"question": question, "choices": choices or [], "multiple": multiple})
        return ("Asked: they see your question, with its choices if it has any, and can answer in their own "
                "words. End your turn: the answer arrives as your next message.")

    @server.tool(description=describe("ask_permission"))
    def ask_permission(what: str, why: str = "", kind: str = "action", folder: str | None = None) -> str:
        call("POST", "/bot/ask_permission", {"what": what, "why": why, "kind": kind, "folder": folder})
        return "Asked the user. End your turn now: their answer arrives as your next message."

    @server.tool(description=describe("review"))
    def review(verdict: str, summary: str, findings: list[dict[str, str]] | None = None) -> str:
        call("POST", "/bot/review", {"verdict": verdict.lower(), "summary": summary, "findings": findings or []})
        return "Verdict recorded. End your reply with a short summary."

    if can_delegate:
        @server.tool(description=describe("next_check"))
        def next_check(minutes: float, why: str) -> str:
            at = call("POST", "/bot/next_check", {"minutes": minutes, "why": why})["at"]
            return f"You'll look around again at {at[11:16]}, unless something comes up first."

    @server.tool(description=describe("set_name"))
    def set_name(name: str) -> str:
        bot = call("POST", "/bot/set_name", {"name": name})
        return f"You go by {bot['display_name']} now: the app shows it for you. Your handle, @{bot['name']}, stays."

    @server.tool(description=describe("write_page"))
    def write_page(page: str, text: str) -> str:
        written = call("POST", "/bot/write_page", {"page": page, "text": text})
        return f"Wrote {written['page']} ({written['path']})."

    @server.tool(description=describe("edit_page"))
    def edit_page(page: str, find: str, replace: str) -> str:
        written = call("POST", "/bot/edit_page", {"page": page, "find": find, "replace": replace})
        return f"Changed {written['page']}."

    @server.tool(description=describe("remember"))
    def remember(title: str, body: str, shared: bool = False) -> str:
        note = call("POST", "/bot/remember", {"title": title, "body": body, "shared": shared})
        return f"Remembered: {note['title']} ({note['path']})."

    @server.tool(description=describe("forget"))
    def forget(name: str, shared: bool = False) -> str:
        note = call("POST", "/bot/forget", {"name": name, "shared": shared})
        return f"Forgot: {note['title']}."

    @server.tool(description=describe("recall"))
    def recall(query: str) -> str:
        return recall_text(call("POST", "/bot/recall", {"query": query}))

    @server.tool(description=describe("propose_routine"))
    def propose_routine(name: str, description: str, goal: str, schedule: str | None = None, on: str | None = None,
                        repo: str | None = None) -> str:
        call("POST", "/bot/propose_routine", {"name": name, "description": description, "goal": goal,
                                              "schedule": schedule, "on": on, "repo": repo})
        return (f"Proposed the routine {name!r}. It's off until the user turns it on in the app (Routines), and its "
                "first run is a test they watch: tell them it's waiting there.")

    @server.tool(description=describe("remind_on"))
    def remind_on(event: str, then: str, match: str = "") -> str:
        intent = call("POST", "/bot/remind_on", {"event": event, "match": match, "then": then})
        return f"Standing intent {intent['id']} set: on {intent['kind']} {intent['match']}".rstrip() + "."

    @server.tool(description=describe("update_state"))
    def update_state(state: str) -> str:
        return f"Kept this task's state ({call('POST', '/bot/update_state', {'state': state})['path']})."

    @server.tool(description=describe("start_thread"))
    def start_thread(title: str, goal: str, project: str | None = None) -> str:
        thread = call("POST", "/bot/start_thread", {"title": title, "goal": goal, "project": project})
        return f"Opened thread {thread['id']}: {thread['title']}. Its result comes back here when it's done."

    @server.tool(description=describe("post"))
    def post(text: str, room: str | None = None) -> str:
        message = call("POST", "/bot/post", {"text": text, "room": room})
        return f"Posted in the group chat ({message['room_id']})."

    @server.tool(description=describe("share"))
    def share(files: list[str], text: str = "", room: str | None = None) -> str:
        return shared_text(call("POST", "/bot/share", {"files": files, "text": text, "room": room}))

    @server.tool(description=describe("open_group"))
    def open_group(title: str, members: list[str], lead: str | None = None) -> str:
        room = call("POST", "/bot/open_group", {"title": title, "members": members, "lead": lead})
        return (f"Opened the group chat {room['title']!r} ({room['id']}) with {', '.join(room['members'])}, led by "
                f"{room['lead']}. Say something there with post(text, room={room['id']!r}).")

    @server.tool(description=describe("add_to_group"))
    def add_to_group(bot: str, room: str | None = None) -> str:
        found = call("POST", "/bot/add_to_group", {"bot": bot, "room": room})
        return f"{bot} is in {found['title']!r} now: @mention them there to bring them in."

    @server.tool(description=describe("ask"))
    def ask(bot: str, question: str) -> str:
        asked = call("POST", "/bot/ask", {"bot": bot, "question": question})
        return f"Asked {asked['bot']} ({asked['id']}). End your turn: the answer arrives as your next message."

    @server.tool(description=describe("delegate"))
    def delegate(bot: str, goal: str, project: str | None = None, workspace: str | None = None,
                 review_of: str | None = None) -> str:
        task = call("POST", "/bot/delegate", {"bot": bot, "goal": goal, "project": project, "workspace": workspace,
                                              "review_of": review_of})
        return f"Delegated {task['id']} to {task['bot']}: {task['title']}"

    @server.tool(description=describe("tasks"))
    def tasks() -> str:
        return json.dumps(call("GET", "/bot/tasks"), indent=1)

    @server.tool(description=describe("message"))
    def message(task_id: str, text: str) -> str:
        result = call("POST", "/bot/message", {"task_id": task_id, "text": text})
        return f"Sent to {result['id']} ({result['status']})."

    if not can_delegate:  # adding bots and proposing merges are a coordinator's
        return server

    @server.tool(description=describe("add_bot"))
    def add_bot(name: str, title: str, description: str, instructions: str, display_name: str | None = None,
                backend: str | None = None, model: str | None = None, effort: str | None = None,
                access: str | None = None, network: bool | None = None, can_delegate: bool = False,
                mcp: list[str] | None = None, skills: list[str] | None = None) -> str:
        fields = {"name": name, "title": title, "description": description, "instructions": instructions,
                  "display_name": display_name, "backend": backend, "model": model, "effort": effort, "access": access,
                  "network": network, "can_delegate": can_delegate, "mcp": mcp, "skills": skills}
        bot = call("POST", "/bot/add_bot", {k: v for k, v in fields.items() if v is not None})
        return (f"Added {bot['label']} (@{bot['name']}), {bot['title']}, to the crew. It's in the roster now: "
                "@mention it in a group chat to bring it in.")

    @server.tool(description=describe("propose_merge"))
    def propose_merge(task_id: str, summary: str) -> str:
        task = call("POST", "/bot/propose_merge", {"task_id": task_id, "summary": summary})
        return (f"Merged {task_id} locally as {task['merged_commit']}." if task["merge_state"] == "merged"
                else f"Proposed {task_id} to the user for merging.")

    @server.tool(description=describe("withdraw_merge"))
    def withdraw_merge(task_id: str, reason: str) -> str:
        call("POST", "/bot/withdraw_merge", {"task_id": task_id, "reason": reason})
        return f"Withdrew the merge proposal for {task_id}."


    return server


def build_review_server(client: httpx.Client, reads_github: bool = False, drafting: bool = True) -> MCPServer:
    """A review's tools instead of the crew's, as `_review_tools` defines them: the pull request itself, drafting line
    comments (the lead), and reading the repository on GitHub when it couldn't be checked out. None posts anything."""
    from crewbots.reviews import _review_tools

    server = MCPServer("crewbots")
    defs = {t.name: t for t in _review_tools(reads_github=True, drafting=True)}

    def call(path: str, body: dict[str, Any]) -> str:
        response = client.post(path, json=body)
        if response.status_code >= 400:
            raise ToolError(response.json().get("detail", response.text))
        return response.json()["text"]

    @server.tool(description=defs["pr_overview"].description)
    def pr_overview() -> str:
        return call("/bot/review/pr_overview", {})

    @server.tool(description=defs["pr_diff"].description)
    def pr_diff(path: str | None = None, part: int = 1) -> str:
        return call("/bot/review/pr_diff", {"path": path, "part": part})

    if drafting:
        @server.tool(description=defs["draft_comment"].description)
        def draft_comment(path: str, line: int, body: str, side: str = "RIGHT") -> str:
            return call("/bot/review/draft_comment", {"path": path, "line": line, "body": body, "side": side})

    if reads_github:
        @server.tool(description=defs["read_file"].description)
        def read_file(path: str, start: int | None = None, end: int | None = None) -> str:
            return call("/bot/review/read_file", {"path": path, "start": start, "end": end})

        @server.tool(description=defs["list_files"].description)
        def list_files(path: str = "") -> str:
            return call("/bot/review/list_files", {"path": path})

    return server


def main() -> None:
    url, token = os.environ["CREWBOTS_URL"], os.environ["CREWBOTS_TOKEN"]
    client = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if os.environ.get("CREWBOTS_TOOLS") == "review":
        flags = set(os.environ.get("CREWBOTS_REVIEW", "").split(","))
        build_review_server(client, reads_github="github" in flags, drafting="drafts" in flags).run("stdio")
    else:
        build_server(client, can_delegate=os.environ.get("CREWBOTS_DELEGATE") == "1").run("stdio")


if __name__ == "__main__":
    main()
