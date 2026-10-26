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

from crewbots.crew_tools import describe, recall_text

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

    @server.tool(description=describe("handoff"))
    def handoff(done: str, output: list[str] | None = None, open: str = "", needs: str = "") -> str:
        call("POST", "/bot/handoff", {"done": done, "output": output or [], "open": open, "needs": needs})
        return "Handoff recorded. End your reply with a short summary."

    @server.tool(description=describe("escalate"))
    def escalate(question: str) -> str:
        call("POST", "/bot/escalate", {"question": question})
        return "Escalated. End your turn now; the answer will arrive as your next message."

    @server.tool(description=describe("review"))
    def review(verdict: str, summary: str, findings: list[dict[str, str]] | None = None) -> str:
        call("POST", "/bot/review", {"verdict": verdict.lower(), "summary": summary, "findings": findings or []})
        return "Verdict recorded. End your reply with a short summary."

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

    @server.tool(description=describe("start_thread"))
    def start_thread(title: str, goal: str) -> str:
        thread = call("POST", "/bot/start_thread", {"title": title, "goal": goal})
        return f"Opened thread {thread['id']}: {thread['title']}. Its result comes back here when it's done."

    @server.tool(description=describe("post"))
    def post(text: str, room: str | None = None) -> str:
        message = call("POST", "/bot/post", {"text": text, "room": room})
        return f"Posted in the group chat ({message['room_id']})."

    @server.tool(description=describe("ask"))
    def ask(bot: str, question: str) -> str:
        asked = call("POST", "/bot/ask", {"bot": bot, "question": question})
        return f"Asked {asked['bot']} ({asked['id']}). End your turn: the answer arrives as your next message."

    @server.tool(description=describe("delegate"))
    def delegate(bot: str, goal: str, workspace: str | None = None, review_of: str | None = None) -> str:
        task = call("POST", "/bot/delegate", {"bot": bot, "goal": goal, "workspace": workspace, "review_of": review_of})
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
        call("POST", "/bot/propose_merge", {"task_id": task_id, "summary": summary})
        return f"Proposed {task_id} to the user for merging."


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
