"""The crewbots MCP server: the tools a bot uses to work in the crew. The CLI starts one per run, over stdio.

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

    @server.tool()
    def handoff(done: str, output: list[str] | None = None, open: str = "", needs: str = "") -> str:
        """Hand your finished work back, as the HANDOFF in team/protocol.md. Call it once, when you are done.

        done: what you completed. output: file paths, branches or links to the output. open: anything unfinished or
        uncertain. needs: what the next step needs, from whoever picks this up.
        """
        call("POST", "/bot/handoff", {"done": done, "output": output or [], "open": open, "needs": needs})
        return "Handoff recorded. End your reply with a short summary."

    @server.tool()
    def escalate(question: str) -> str:
        """Ask for a decision that is not yours to make (team/rules.md). Ask one clear question, then end your turn:
        the answer arrives as your next message."""
        call("POST", "/bot/escalate", {"question": question})
        return "Escalated. End your turn now; the answer will arrive as your next message."

    @server.tool()
    def review(verdict: str, summary: str, findings: list[dict[str, str]] | None = None) -> str:
        """Give your verdict, when your task is a review. The verdict is what counts, not your prose.

        verdict: "pass" or "fail". summary: one or two sentences. findings: one per problem, each
        {"location": "file:line", "problem": "...", "scenario": "the input or state, and the wrong result"}. A pass
        with remaining risks is still a pass; list the risks as findings.
        """
        call("POST", "/bot/review", {"verdict": verdict.lower(), "summary": summary, "findings": findings or []})
        return "Verdict recorded. End your reply with a short summary."

    if not can_delegate:
        return server

    @server.tool()
    def propose_merge(task_id: str, summary: str) -> str:
        """Put approved work in front of the user to merge. Every review of its current version must have passed.
        You never merge; the user does.

        summary: what the change does and how it was verified, for the user deciding whether to merge.
        """
        call("POST", "/bot/propose_merge", {"task_id": task_id, "summary": summary})
        return f"Proposed {task_id} to the user for merging."

    @server.tool()
    def delegate(bot: str, goal: str, workspace: str | None = None, review_of: str | None = None) -> str:
        """Give one task to one bot in the crew. It starts at once and runs in parallel with other delegated tasks.

        goal: the outcome, the context the bot needs, and what done means; it must stand alone. workspace: a folder or
        repo to work in, if not the one this task works in. review_of: the id of a task whose work this one reviews;
        the reviewer then works in that task's worktree.
        After delegating, end your turn. Results arrive as your next message once none of your tasks are running.
        """
        task = call("POST", "/bot/delegate", {"bot": bot, "goal": goal, "workspace": workspace, "review_of": review_of})
        return f"Delegated {task['id']} to {task['bot']}: {task['title']}"

    @server.tool()
    def tasks() -> str:
        """The tasks you delegated: status, worktree, handoff and final message for each."""
        return json.dumps(call("GET", "/bot/tasks"), indent=1)

    @server.tool()
    def message(task_id: str, text: str) -> str:
        """Send a message to a task you delegated: answer its escalation, or send it review objections to fix. Its
        next result comes back to you like the first one."""
        result = call("POST", "/bot/message", {"task_id": task_id, "text": text})
        return f"Sent to {result['id']} ({result['status']})."

    return server


def main() -> None:
    url, token = os.environ["CREWBOTS_URL"], os.environ["CREWBOTS_TOKEN"]
    client = httpx.Client(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    build_server(client, can_delegate=os.environ.get("CREWBOTS_DELEGATE") == "1").run("stdio")


if __name__ == "__main__":
    main()
