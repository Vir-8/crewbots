"""The crewbots MCP server: the crew tools (crewbots.crew_tools) for backends that can't host them in-process, which
is Codex. The CLI starts one per session, over stdio.

It holds nothing but a run token (CREWBOTS_TOKEN) and the daemon's address (CREWBOTS_URL). Every tool is a call to the
daemon's /bot/tools/{name}, which decides what this task may do; this process only translates. Coordinators also get
the tools for adding bots and proposing merges (CREWBOTS_DELEGATE=1), which the daemon refuses anyone else regardless.
"""

import asyncio
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent

from crewbots import webkit
from crewbots.crew_tools import SPECS, describe, tools_for

INSTRUCTIONS = "Tools for working in a crewbots crew: hand work back, escalate decisions, and (for coordinators) delegate."
# Long enough for the first check, which builds the helper too
WEBKIT_TIMEOUT = webkit.BUILD_TIMEOUT_S + webkit.MAX_WAIT_S + webkit.TIMEOUT_S + 30


class CrewError(ToolError):
    """The daemon refused or failed. A ToolError, so the bot sees the reason; any other exception reaches the model
    only as "Error executing tool"."""


def _call(client: httpx.Client, path: str, body: dict[str, Any], **options: Any) -> Any:
    response = client.post(path, json=body, **options)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise CrewError(f"crewbots refused this ({response.status_code}): {detail}")
    return response.json()


class _Daemon(MCPServer):
    """Sends each tool call to the daemon with the arguments as the bot gave them, to be read there as Claude's are
    (crewbots.tool_args): under the other names a bot reaches for, a list sent as one string, and with a note of any
    it doesn't take. The tools registered on it only declare what Codex is shown."""

    def __init__(self, client: httpx.Client):
        super().__init__(name="crewbots", instructions=INSTRUCTIONS)
        self.client = client

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> CallToolResult:
        if name not in {tool.name for tool in await self.list_tools()}:
            raise ToolError(f"Unknown tool: {name}")
        options = {"timeout": WEBKIT_TIMEOUT} if name == "webkit_check" else {}
        content = (await asyncio.to_thread(_call, self.client, f"/bot/tools/{name}", arguments, **options))["content"]
        if isinstance(content, list):  # the browser's: text and images
            return CallToolResult(content=[ImageContent(**item) if item["type"] == "image" else TextContent(**item)
                                           for item in content])
        # Also as the structured result that a `-> str` tool's output schema promises
        return CallToolResult(content=[TextContent(type="text", text=content)], structured_content={"result": content})


def build_server(client: httpx.Client, can_delegate: bool, *, is_chief: bool = False, kind: str | None = None,
                 reviewing: bool = False, unity: bool = False, shared_app: bool = False) -> MCPServer:
    """The crew tools for one task: those `crew_tools.tools_for` gives it, as Claude's bots get them."""
    server = _crew_server(client)
    given = {tool.name for tool in tools_for(can_delegate, is_chief=is_chief, kind=kind, reviewing=reviewing,
                                             unity=unity, shared_app=shared_app)}
    for name in SPECS.keys() - given:
        server.remove_tool(name)
    return server


def _crew_server(client: httpx.Client) -> MCPServer:
    server = _Daemon(client)

    @server.tool(description=describe("browser_open"))
    def browser_open(url: str) -> list[TextContent | ImageContent]: ...

    @server.tool(description=describe("browser_screenshot"))
    def browser_screenshot(width: int = 1400, height: int = 860, theme: str = "light",
                           full_page: bool = False) -> list[TextContent | ImageContent]: ...

    @server.tool(description=describe("browser_click"))
    def browser_click(selector: str) -> list[TextContent | ImageContent]: ...

    @server.tool(description=describe("browser_type"))
    def browser_type(selector: str | None = None, text: str | None = None,
                     key: str | None = None) -> list[TextContent | ImageContent]: ...

    @server.tool(description=describe("browser_evaluate"))
    def browser_evaluate(expression: str) -> list[TextContent | ImageContent]: ...

    @server.tool(description=describe("browser_console"))
    def browser_console(clear: bool = False) -> list[TextContent | ImageContent]: ...

    @server.tool(description=describe("webkit_check"))
    def webkit_check(url: str, click: str | None = None, setup: str | None = None, js: str | None = None,
                     wait: float | None = None, width: int | None = None, height: int | None = None) -> str: ...

    @server.tool(description=describe("handoff"))
    def handoff(done: str, output: list[str] | str | None = None, open: str = "", needs: str = "",
                evidence: list[str] | str | None = None, checks: list[str] | str | None = None) -> str: ...

    @server.tool(description=describe("escalate"))
    def escalate(question: str, choices: list[dict[str, str]] | None = None, multiple: bool = False) -> str: ...

    @server.tool(description=describe("ask_permission"))
    def ask_permission(what: str, why: str = "", kind: str = "action", folder: str | None = None,
                       task_id: str | None = None) -> str: ...

    @server.tool(description=describe("review"))
    def review(verdict: str, summary: str, findings: list[dict[str, str]] | None = None) -> str: ...

    @server.tool(description=describe("next_check"))
    def next_check(minutes: float, why: str) -> str: ...

    @server.tool(description=describe("set_name"))
    def set_name(name: str) -> str: ...

    @server.tool(description=describe("write_page"))
    def write_page(page: str, content: str) -> str: ...

    @server.tool(description=describe("edit_page"))
    def edit_page(page: str, find: str, replace: str) -> str: ...

    @server.tool(description=describe("remember"))
    def remember(title: str, body: str, shared: bool = False, mistake: str = "", check: str = "") -> str: ...

    @server.tool(description=describe("forget"))
    def forget(name: str, shared: bool = False) -> str: ...

    @server.tool(description=describe("recall"))
    def recall(query: str) -> str: ...

    @server.tool(description=describe("propose_routine"))
    def propose_routine(name: str, description: str, goal: str, schedule: str | None = None, on: str | None = None,
                        repo: str | None = None) -> str: ...

    @server.tool(description=describe("remind_on"))
    def remind_on(event: str, then: str, match: str = "") -> str: ...

    @server.tool(description=describe("working_state"))
    def working_state(section: str = "", action: str = "", text: str = "", entry_id: str = "",
                      done: bool = False) -> str: ...

    @server.tool(description=describe("update_state"))
    def update_state(state: str) -> str: ...

    @server.tool(description=describe("start_thread"))
    def start_thread(title: str, goal: str, project: str | None = None) -> str: ...

    @server.tool(description=describe("post"))
    def post(text: str, room: str | None = None) -> str: ...

    @server.tool(description=describe("open_unity_editor"))
    def open_unity_editor() -> str: ...

    @server.tool(description=describe("release_app"))
    def release_app() -> str: ...

    @server.tool(description=describe("share"))
    def share(files: list[str], text: str = "", room: str | None = None) -> str: ...

    @server.tool(description=describe("open_group"))
    def open_group(title: str, members: list[str], lead: str | None = None) -> str: ...

    @server.tool(description=describe("add_to_group"))
    def add_to_group(bot: str, room: str | None = None) -> str: ...

    @server.tool(description=describe("ask"))
    def ask(bot: str, question: str) -> str: ...

    @server.tool(description=describe("delegate"))
    def delegate(bot: str, goal: str, project: str | None = None, workspace: str | None = None,
                 review_of: str | None = None) -> str: ...

    @server.tool(description=describe("tasks"))
    def tasks() -> str: ...

    @server.tool(description=describe("message"))
    def message(task_id: str, text: str) -> str: ...

    @server.tool(description=describe("stop_task"))
    def stop_task(task_id: str) -> str: ...

    @server.tool(description=describe("room_status"))
    def room_status(room: str | None = None) -> str: ...

    @server.tool(description=describe("sweep_worktrees"))
    def sweep_worktrees(dry_run: bool = True) -> str: ...

    @server.tool(description=describe("add_bot"))
    def add_bot(name: str, title: str, description: str, instructions: str, display_name: str | None = None,
                backend: str | None = None, model: str | None = None, effort: str | None = None,
                access: str | None = None, network: bool | None = None, coordinator: bool = False,
                mcp: list[str] | None = None, skills: list[str] | None = None) -> str: ...

    @server.tool(description=describe("tell_user"))
    def tell_user(text: str, needs_you: bool = False) -> str: ...

    @server.tool(description=describe("propose_push"))
    def propose_push(project: str, summary: str) -> str: ...

    @server.tool(description=describe("propose_replace_main"))
    def propose_replace_main(project: str, branch: str, summary: str) -> str: ...

    @server.tool(description=describe("propose_merge"))
    def propose_merge(task_id: str, summary: str) -> str: ...

    @server.tool(description=describe("drop_task"))
    def drop_task(task_id: str, reason: str) -> str: ...

    @server.tool(description=describe("withdraw_merge"))
    def withdraw_merge(task_id: str, reason: str) -> str: ...

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
        def on(name: str) -> bool:
            return os.environ.get(f"CREWBOTS_{name}") == "1"

        build_server(client, can_delegate=on("DELEGATE"), is_chief=on("CHIEF"),
                     kind=os.environ.get("CREWBOTS_KIND") or None, reviewing=on("REVIEWING"), unity=on("UNITY"),
                     shared_app=on("SHARED_APP")).run("stdio")


if __name__ == "__main__":
    main()
