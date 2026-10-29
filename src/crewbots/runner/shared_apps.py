"""MCP servers that drive one running app, such as a Unity Editor: every bot's session can connect to it, but there is
one play mode, one test run and one open scene, so two tasks at once would undo each other. A server marked
`one_at_a_time` in mcp.json reaches bots through the daemon (`/v1/bot/mcp/<server>`), which lets one task's calls
through at a time. A task holds the app from its first tool call while its turn runs, until it has made no call for
APP_IDLE_S; another's calls meanwhile are answered that it's busy, and by whom, and the first one turned away is told
when it's free. Listing tools and reading resources never wait, and nor does a task in a worktree of its own, whose
app is its own: an Editor opened on that worktree, as Unity opens a folder in one Editor only."""

import json
import time
from pathlib import Path
from typing import Any

from crewbots import paths
from crewbots.backends import McpServer
from crewbots.crew import Bot
from crewbots.runner.common import NotAllowed
from crewbots.store import Status

APP_IDLE_S = 5 * 60  # a task keeps a shared app this long after its last call, while its turn runs
WAKE_AGAIN_S = 2 * 60  # a task told the app is free that hasn't taken it by then: the next in line is told


class SharedApps:
    """The part of Runner that lets one task at a time drive a shared app's MCP server."""

    @property
    def _apps(self) -> dict[str, dict[str, Any]]:
        return self.__dict__.setdefault("_app_holders", {})

    @property
    def _app_queue(self) -> dict[str, list[str]]:
        return self.__dict__.setdefault("_app_waiting", {})

    def _mcp_of(self, bot: Bot, token: str) -> dict[str, McpServer]:
        """The bot's MCP servers for a run, a shared app's reached through the daemon as this run's task."""
        if not self.api_url:
            return bot.mcp
        return {name: McpServer(url=f"{self.api_url}/bot/mcp/{name}", headers={"Authorization": f"Bearer {token}"},
                                transport="http") if server.one_at_a_time else server
                for name, server in bot.mcp.items()}

    def shared_app(self, task_id: str, name: str) -> McpServer:
        bot = self.bot_for(self.store.get_task(task_id).bot)
        server = bot.mcp.get(name) if bot else None
        if server is None or not server.one_at_a_time:
            raise NotAllowed(f"no shared app {name!r} for this bot")
        return server

    def app_call(self, task_id: str, name: str, body: bytes) -> dict[str, Any] | None:
        """None when the request may go on to the app; otherwise the JSON-RPC answer that it's busy. Only tool calls
        take the app."""
        try:
            sent = json.loads(body or b"null")
        except ValueError:
            return None
        calls = [m for m in (sent if isinstance(sent, list) else [sent])
                 if isinstance(m, dict) and m.get("method") == "tools/call"]
        if not calls or self._own_checkout(task_id):
            return None
        now = time.monotonic()
        holder = self._apps.get(name)
        if holder is None or holder["task"] == task_id or not self._holding(holder, now):
            self._apps[name] = {"task": task_id, "since": holder["since"] if holder and holder["task"] == task_id
                                else now, "last": now}
            if task_id in (queue := self._app_queue.get(name, [])):
                queue.remove(task_id)
            return None
        queue = self._app_queue.setdefault(name, [])
        if task_id not in queue:
            queue.append(task_id)
        who = self.store.get_task(holder["task"])
        busy = (f"{name} is in use by {who.bot} ({who.id}), for {max(1, round((now - holder['since']) / 60))} min so far. "
                "It drives one running app, so one task uses it at a time. Do what doesn't need it, or end your "
                "turn: crewbots tells you when it's free.")
        return {"jsonrpc": "2.0", "id": calls[0].get("id"),
                "result": {"content": [{"type": "text", "text": busy}], "isError": True}}

    def _own_checkout(self, task_id: str) -> bool:
        """Whether the task works in a worktree of its own, or reviews one in place."""
        task = self.store.get_task(task_id)
        folder = task.worktree_path if task.worktree_path and not task.base_branch else task.workspace
        root = (self.worktree_root or paths.worktrees()).resolve()
        return bool(folder) and Path(folder).resolve().is_relative_to(root)

    def _holding(self, holder: dict[str, Any], now: float) -> bool:
        return holder["task"] in self._runs and now - holder["last"] < APP_IDLE_S

    def free_apps(self, ended: str | None = None) -> None:
        """Lets go of apps whose task's turn is over (`ended`, or found so) or has gone quiet on them, and tells the
        next task in line. Run as turns end and on the scheduler's tick."""
        now = time.monotonic()
        for name in {*self._apps, *self._app_queue}:
            holder = self._apps.get(name)
            if holder and (holder["task"] == ended or not self._holding(holder, now)):
                del self._apps[name]
                holder = None
            queue = self._app_queue.get(name, [])
            if holder or not queue:
                continue
            told = self.__dict__.setdefault("_app_told", {})
            if name in told and now - told[name] < WAKE_AGAIN_S:
                continue  # the one told last may yet take it
            for task_id in list(queue):
                task = self.store.get_task(task_id)
                if task.status in (Status.FAILED, Status.CANCELLED):
                    queue.remove(task_id)
                elif task.status in (Status.IDLE, Status.RUNNING, Status.QUEUED):  # not one waiting on an answer
                    queue.remove(task_id)
                    told[name] = now
                    try:
                        self.send(task_id, f"{name} is free now: go on with what needed it.", sender="crewbots")
                    except NotAllowed:
                        continue
                    break
