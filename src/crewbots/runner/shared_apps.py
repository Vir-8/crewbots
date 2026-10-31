"""MCP servers that drive one running app, such as Blender or a Unity Editor: every bot's session can connect to it,
but there is one open scene and one undo history (and for Unity one play mode and one test run), so two tasks at once
would undo each other. A server marked `one_at_a_time` in mcp.json reaches bots through the daemon
(`/v1/bot/mcp/<server>`), which lets one task's calls through at a time. A task holds the app from its first tool call
while its turn runs, until it lets go (`release_app`) or has made no call for APP_IDLE_S; another's calls meanwhile are
answered that it's busy, and by whom, and the first one turned away is told when it's free. Listing tools and reading
resources never wait.

Unity opens a folder in one Editor only, so a task in a Unity project drives the Editor open on its folder through the
Unity MCP: tasks in different Unity projects don't wait for each other, and nor does a task in a worktree of its own,
whose Editor is its own. Any other server, and the Unity MCP from outside a Unity project, can reach whichever app is
running, so a task waits for any holder of the server, and while it uses it no one else does."""

import json
import time
from pathlib import Path
from typing import Any

from crewbots.backends import McpServer
from crewbots.crew import Bot
from crewbots.projects import is_unity
from crewbots.runner.common import NotAllowed
from crewbots.store import Status

APP_IDLE_S = 5 * 60  # a task keeps a shared app this long after its last call, while its turn runs
WAKE_AGAIN_S = 2 * 60  # a task told the app is free that hasn't taken it by then: the next in line is told


class SharedApps:
    """The part of Runner that lets one task at a time drive a shared app's MCP server."""

    @property
    def _apps(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Who holds each app: by server, and the Unity project whose Editor it is ("" for any app the server reaches)."""
        return self.__dict__.setdefault("_app_holders", {})

    @property
    def _app_queue(self) -> dict[tuple[str, str], list[str]]:
        return self.__dict__.setdefault("_app_waiting", {})

    def _waiting_for_app(self, task_id: str) -> bool:
        return any(task_id in queue for queue in self._app_queue.values())

    def _app_of(self, task_id: str, name: str) -> tuple[str, str]:
        """The app a task's calls to `name` drive: for the Unity MCP in a Unity project, the Editor open on its folder;
        otherwise any."""
        folder = self._unity_project(task_id, name)
        return name, str(folder) if folder else ""

    def _unity_project(self, task_id: str, name: str) -> Path | None:
        """The Unity project the task works in, if its folder is one and `name` is its bot's Unity MCP."""
        task = self.store.get_task(task_id)
        server = self.shared_app(task_id, name)
        if server.url != self._unity_mcp_url(task):
            return None
        folder = task.worktree_path or task.workspace
        path = Path(folder).expanduser().resolve() if folder else None
        return path if path and is_unity(path) else None

    def _mcp_of(self, bot: Bot, token: str) -> dict[str, McpServer]:
        """The bot's MCP servers for a run, a shared app's reached through the daemon as this run's task."""
        if not self.api_url:
            return bot.mcp
        return {name: McpServer(url=f"{self.api_url}/bot/mcp/{name}", headers={"Authorization": f"Bearer {token}"},
                                transport="http") if server.one_at_a_time else server
                for name, server in bot.mcp.items()}

    def has_shared_app(self, bot_name: str) -> bool:
        bot = self.bot_for(bot_name)
        return bool(bot) and any(server.one_at_a_time for server in bot.mcp.values())

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
        if not calls:
            return None
        app = self._app_of(task_id, name)
        if app[1] and self._in_own_worktree(app[1]):  # a Unity worktree of its own: no one else drives its Editor
            return None
        now = time.monotonic()
        if (held := self._in_the_way(app, now, task_id)) is None:
            holder = self._apps.get(app)
            self._apps[app] = {"task": task_id, "since": holder["since"] if holder and holder["task"] == task_id
                               else now, "last": now}
            if task_id in (queue := self._app_queue.get(app, [])):
                queue.remove(task_id)
            self.__dict__.get("_app_told", {}).pop(app, None)  # whoever was told has had its chance
            return None
        queue = self._app_queue.setdefault(app, [])
        if task_id not in queue:
            queue.append(task_id)
        held_app, holder = held
        who, me = self.store.get_task(holder["task"]), self.store.get_task(task_id)
        by = (f"your own task {who.id}, which has that work: leave it to that task" if who.bot == me.bot
              else f"{who.bot} ({who.id})")
        busy = (f"{name} is in use{_for(held_app[1])} by {by}, for "
                f"{max(1, round((now - holder['since']) / 60))} min so far. It drives one running app, so one task "
                "uses it at a time. Do what doesn't need it, or end your turn: crewbots tells you when it's free, so "
                "there's no need to ask anyone to.")
        return {"jsonrpc": "2.0", "id": calls[0].get("id"),
                "result": {"content": [{"type": "text", "text": busy}], "isError": True}}

    def _in_the_way(self, app: tuple[str, str], now: float,
                    task_id: str | None = None) -> tuple[tuple[str, str], dict[str, Any]] | None:
        """Another task's hold on an app that `app`'s calls could drive: its Unity project's Editor, or any for a task
        outside one."""
        name, folder = app
        for held, holder in self._apps.items():
            same = held[0] == name and (folder == held[1] or "" in (folder, held[1]))
            if same and holder["task"] != task_id and self._holding(holder, now):
                return held, holder
        return None

    def _holding(self, holder: dict[str, Any], now: float) -> bool:
        return holder["task"] in self._runs and now - holder["last"] < APP_IDLE_S

    def release_app(self, task_id: str) -> str:
        """The `release_app` tool: the task lets go of the shared apps it holds before its turn ends."""
        held = [f"{name}{_for(folder)}" for (name, folder), holder in self._apps.items() if holder["task"] == task_id]
        if not held:
            return "You don't hold a shared app now, so there's nothing to let go of."
        self.free_apps(ended=task_id)
        return (f"Let go of {', '.join(held)}: the next task waiting for it is told now. A call of yours to it takes it "
                "back only if no one else has it.")

    def free_apps(self, ended: str | None = None) -> None:
        """Lets go of apps whose task's turn is over or that it let go of (`ended`, or found so), or that it has gone
        quiet on, and tells the next task in line for each that nothing holds now. Run as turns end and on the
        scheduler's tick."""
        now = time.monotonic()
        for app, holder in list(self._apps.items()):
            if holder["task"] == ended or not self._holding(holder, now):
                del self._apps[app]
        told = self.__dict__.setdefault("_app_told", {})
        for app, queue in self._app_queue.items():
            if not queue or self._in_the_way(app, now):
                continue
            if app in told and now - told[app] < WAKE_AGAIN_S:
                continue  # the one told last may yet take it
            for task_id in list(queue):
                task = self.store.get_task(task_id)
                if task.status in (Status.FAILED, Status.CANCELLED):
                    queue.remove(task_id)
                elif task.status in (Status.IDLE, Status.RUNNING, Status.QUEUED):  # not one waiting on an answer
                    queue.remove(task_id)
                    told[app] = now
                    try:
                        self.send(task_id, f"{app[0]} is free now{_for(app[1])}: go on with what needed it.",
                                  sender="crewbots")
                    except NotAllowed:
                        continue
                    break


def _for(folder: str) -> str:
    return f" for {Path(folder).name}" if folder else ""
