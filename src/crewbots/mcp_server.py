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


def main() -> None:
    url, token = os.environ["CREWBOTS_URL"], os.environ["CREWBOTS_TOKEN"]
    client = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    build_server(client, can_delegate=os.environ.get("CREWBOTS_DELEGATE") == "1").run("stdio")


if __name__ == "__main__":
    main()
