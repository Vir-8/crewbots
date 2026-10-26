"""The crewbots MCP server: the crew tools (crewbots.crew_tools) for backends that can't host them in-process, which
is Codex. The CLI starts one per session, over stdio.

It holds nothing but a run token (CREWBOTS_TOKEN) and the daemon's address (CREWBOTS_URL). Every tool is a call to the
daemon's /bot/ API, which decides what this task may do; this process only translates. Coordinators also get the
delegation tools (CREWBOTS_DELEGATE=1), but the daemon refuses delegation from anyone else regardless.
"""

import json
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from crewbots.crew_tools import BY_NAME, recall_text

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

    @server.tool(description=BY_NAME["handoff"].description)
    def handoff(done: str, output: list[str] | None = None, open: str = "", needs: str = "") -> str:
        call("POST", "/bot/handoff", {"done": done, "output": output or [], "open": open, "needs": needs})
        return "Handoff recorded. End your reply with a short summary."

    @server.tool(description=BY_NAME["escalate"].description)
    def escalate(question: str) -> str:
        call("POST", "/bot/escalate", {"question": question})
        return "Escalated. End your turn now; the answer will arrive as your next message."

    @server.tool(description=BY_NAME["review"].description)
    def review(verdict: str, summary: str, findings: list[dict[str, str]] | None = None) -> str:
        call("POST", "/bot/review", {"verdict": verdict.lower(), "summary": summary, "findings": findings or []})
        return "Verdict recorded. End your reply with a short summary."

    @server.tool(description=BY_NAME["remember"].description)
    def remember(title: str, body: str, shared: bool = False) -> str:
        note = call("POST", "/bot/remember", {"title": title, "body": body, "shared": shared})
        return f"Remembered: {note['title']} ({note['path']})."

    @server.tool(description=BY_NAME["forget"].description)
    def forget(name: str, shared: bool = False) -> str:
        note = call("POST", "/bot/forget", {"name": name, "shared": shared})
        return f"Forgot: {note['title']}."

    @server.tool(description=BY_NAME["recall"].description)
    def recall(query: str) -> str:
        return recall_text(call("POST", "/bot/recall", {"query": query}))

    @server.tool(description=BY_NAME["start_thread"].description)
    def start_thread(title: str, goal: str) -> str:
        thread = call("POST", "/bot/start_thread", {"title": title, "goal": goal})
        return f"Opened thread {thread['id']}: {thread['title']}. Its result comes back here when it's done."

    @server.tool(description=BY_NAME["post"].description)
    def post(text: str, room: str | None = None) -> str:
        message = call("POST", "/bot/post", {"text": text, "room": room})
        return f"Posted in the group chat ({message['room_id']})."

    if not can_delegate:
        return server

    @server.tool(description=BY_NAME["propose_merge"].description)
    def propose_merge(task_id: str, summary: str) -> str:
        call("POST", "/bot/propose_merge", {"task_id": task_id, "summary": summary})
        return f"Proposed {task_id} to the user for merging."

    @server.tool(description=BY_NAME["delegate"].description)
    def delegate(bot: str, goal: str, workspace: str | None = None, review_of: str | None = None) -> str:
        task = call("POST", "/bot/delegate", {"bot": bot, "goal": goal, "workspace": workspace, "review_of": review_of})
        return f"Delegated {task['id']} to {task['bot']}: {task['title']}"

    @server.tool(description=BY_NAME["tasks"].description)
    def tasks() -> str:
        return json.dumps(call("GET", "/bot/tasks"), indent=1)

    @server.tool(description=BY_NAME["message"].description)
    def message(task_id: str, text: str) -> str:
        result = call("POST", "/bot/message", {"task_id": task_id, "text": text})
        return f"Sent to {result['id']} ({result['status']})."

    return server


def build_review_server(client: httpx.Client) -> MCPServer:
    """A code reviewer's tools instead of the crew's: reading the repository on GitHub at the pull request's head."""
    from crewbots.reviews import _review_tools

    server = MCPServer("crewbots")
    defs = {t.name: t for t in _review_tools()}

    def call(path: str, body: dict[str, Any]) -> str:
        response = client.post(path, json=body)
        if response.status_code >= 400:
            raise ToolError(response.json().get("detail", response.text))
        return response.json()["text"]

    @server.tool(description=defs["read_file"].description)
    def read_file(path: str, start: int | None = None, end: int | None = None) -> str:
        return call("/bot/review/read_file", {"path": path, "start": start, "end": end})

    @server.tool(description=defs["list_files"].description)
    def list_files(path: str = "") -> str:
        return call("/bot/review/list_files", {"path": path})

    @server.tool(description=defs["search_code"].description)
    def search_code(query: str) -> str:
        return call("/bot/review/search_code", {"query": query})

    return server


def main() -> None:
    url, token = os.environ["CREWBOTS_URL"], os.environ["CREWBOTS_TOKEN"]
    client = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if os.environ.get("CREWBOTS_TOOLS") == "review":
        build_review_server(client).run("stdio")
    else:
        build_server(client, can_delegate=os.environ.get("CREWBOTS_DELEGATE") == "1").run("stdio")


if __name__ == "__main__":
    main()
