"""The bot-token API: what a bot's crewbots MCP server calls, acting as its own task and nothing else."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from crewbots.api.context import Context
from crewbots.crew_tools import CrewTools, ToolRefused


def routes(c: Context) -> APIRouter:
    runner = c.runner
    v1 = APIRouter()

    @v1.post("/bot/tools/{name}")
    async def bot_tool(request: Request, name: str, body: dict[str, Any]):
        """Any crew tool, given what the bot sent it: read and run as the in-process tools are."""
        task = request.state.task
        try:
            content = await CrewTools(runner, task.id, runner.is_coordinator(task.bot)).call(name, body)
        except ToolRefused as e:
            raise HTTPException(422, str(e)) from None
        return {"content": content}

    @v1.post("/bot/review/{tool}")
    async def bot_review_tool(request: Request, tool: str, body: dict[str, Any]):
        """A reviewer's tools, for one that reaches crewbots over MCP. It can only read and draft on its own pull
        request; nothing here posts to GitHub."""
        task = request.state.task
        if task.kind != "review" or not task.pr:
            raise HTTPException(403, "only a code review has these tools")
        try:
            text = await runner.review_tools(task).call(tool, {k: v for k, v in body.items() if v is not None})
        except ToolRefused as e:
            raise HTTPException(422, str(e)) from None
        return {"text": text}

    return v1
