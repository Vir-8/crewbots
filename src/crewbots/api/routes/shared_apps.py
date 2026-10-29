"""A shared app's MCP server, reached by a bot through the daemon (runner/shared_apps.py): the bot's own MCP client
talks to this route as if it were the server, with its run token, and everything goes on to the real one, except a
tool call while another task has the app, which is answered that it's busy."""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from crewbots.api.context import Context

SENT = ("accept", "content-type", "mcp-session-id", "mcp-protocol-version", "last-event-id")  # on to the app
RETURNED = ("content-type", "mcp-session-id", "cache-control")  # back to the bot
TRANSPORT: httpx.AsyncBaseTransport | None = None  # what reaches the apps: tests stand one in


def routes(c: Context) -> APIRouter:
    v1, runner = APIRouter(), c.runner
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10), transport=TRANSPORT)  # a stream stays open

    @v1.api_route("/bot/mcp/{name}", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    async def shared_app(name: str, request: Request):
        task = request.state.task
        server = runner.shared_app(task.id, name)
        body = await request.body()
        if request.method == "POST" and (busy := runner.app_call(task.id, name, body)):
            session = request.headers.get("mcp-session-id")
            return JSONResponse(busy, headers={"mcp-session-id": session} if session else None)
        headers = {k: v for k, v in request.headers.items() if k.lower() in SENT} | server.headers
        try:
            answer = await client.send(client.build_request(request.method, server.url, content=body, headers=headers,
                                                            params=request.query_params), stream=True)
        except httpx.HTTPError as e:
            return JSONResponse({"detail": f"{name} didn't answer: {e}"}, status_code=502)
        return StreamingResponse(answer.aiter_bytes(), status_code=answer.status_code,
                                 headers={k: v for k, v in answer.headers.items() if k.lower() in RETURNED},
                                 background=BackgroundTask(answer.aclose))

    return v1
