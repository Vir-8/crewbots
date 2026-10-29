"""Unity Editors for tasks in a worktree of their own (unity.py): a task asks for one with `open_unity_editor`, is told
when it has connected to the Unity MCP, and crewbots closes it once the work is done with: before its worktree goes,
and when nothing has used it for EDITOR_IDLE_S. A turn the task ends to wait for its Editor isn't reported to whoever
handed it out: the task goes on once it's told, so there's nothing yet to hand back. At most MAX_EDITORS are open at
once, as each takes a few GB of memory: an idle one is closed to make room, or the task is told when there is some."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from crewbots import paths, unity
from crewbots.projects import is_unity
from crewbots.runner.common import NotAllowed
from crewbots.store import Task

MAX_EDITORS = 2
EDITOR_IDLE_S = 30 * 60
CONNECT_S = 30 * 60  # a first open imports the whole project
CONNECT_EVERY_S = 5

log = logging.getLogger(__name__)


class Editors:
    """The part of Runner that opens and closes Unity Editors for worktree tasks."""

    @property
    def _editor_waits(self) -> list[str]:
        return self.__dict__.setdefault("_editor_waiting", [])

    @property
    def _editor_opening(self) -> set[str]:
        return self.__dict__.setdefault("_editors_opening", set())

    def _waiting_for_editor(self, task_id: str) -> bool:
        return task_id in self._editor_opening

    def open_unity_editor(self, task_id: str) -> str:
        task = self.store.get_task(task_id)
        folder = self._unity_folder(task)
        if folder in (open_now := self._crew_editors()):
            return f"Unity is already open on your worktree: it's {folder.name}@… in mcpforunity://instances."
        app = unity.editor_app(folder)
        if app is None:
            raise NotAllowed(f"Unity {unity.version(folder) or '(no version in ProjectSettings)'} isn't installed where "
                             "Unity Hub keeps it: ask the user to install it")
        if len(open_now) >= MAX_EDITORS:
            idle = [f for f in open_now if not self._editor_in_use(f)]
            if not idle:
                if task_id not in self._editor_waits:
                    self._editor_waits.append(task_id)
                return (f"{MAX_EDITORS} Unity Editors are open for other tasks ({', '.join(f.name for f in open_now)}), "
                        "the most at once. Do what doesn't need one, or end your turn: crewbots tells you when there's "
                        "room, and you ask again.")
            unity.close_editor(open_now[idle[0]])
        unity.open_editor(app, folder)
        self._record(task_id, kind="unity_editor", data={"opened": str(folder)})
        url = self._unity_mcp_url(task)
        if url is None:
            return ("Opening Unity on your worktree. crewbots can't see a Unity MCP server among yours, so it can't tell "
                    "you when it's connected.")
        self._watch_editor(task_id, folder, url)
        return ("Opening Unity on your worktree. Its first open imports the whole project, which can take a while; then "
                "it connects to the Unity MCP by itself. Do what doesn't need it, or end your turn: crewbots tells you "
                "when it's connected.")

    def _watch_editor(self, task_id: str, folder: Path, url: str) -> None:
        self._editor_opening.add(task_id)
        watch = asyncio.get_running_loop().create_task(self._await_editor(task_id, folder, url))
        self.__dict__.setdefault("_editor_watches", set()).add(watch)
        watch.add_done_callback(self.__dict__["_editor_watches"].discard)

    def resume_editor_watches(self) -> None:
        """On start: Editors opened for a task that hadn't been told it was connected yet are watched again."""
        for folder in self._crew_editors():
            try:
                task = self.store.get_task(folder.name)
            except KeyError:
                continue
            if not (opened := self.store.last_event(task.id, ("unity_editor",))):
                continue
            told = any(e.kind == "message" and e.data.get("from") == "crewbots" and "Unity is open on your worktree"
                       in e.data.get("text", "") for e in self.store.events(task.id, after=opened.id))
            if not told and (url := self._unity_mcp_url(task)):
                self._watch_editor(task.id, folder, url)

    async def _await_editor(self, task_id: str, folder: Path, url: str) -> None:
        try:
            await self._editor_ready(task_id, folder, url)
        finally:
            self._editor_opening.discard(task_id)

    async def _editor_ready(self, task_id: str, folder: Path, url: str) -> None:
        end = time.monotonic() + CONNECT_S
        while time.monotonic() < end:
            await asyncio.sleep(CONNECT_EVERY_S)
            if folder not in self._crew_editors():
                self.send(task_id, "Unity closed on your worktree before it connected: ask for it again with "
                          "open_unity_editor, or say what's wrong.", sender="crewbots")
                return
            try:
                if await unity.connected(url, folder.name):
                    self.send(task_id, f"Unity is open on your worktree and connected: set_active_instance to its "
                              f"{folder.name}@… id from mcpforunity://instances, and go on.", sender="crewbots")
                    return
            except Exception as e:  # the server's down or restarting: look again
                log.debug("Couldn't ask the Unity MCP about %s: %s", folder, e)
        self.send(task_id, f"Unity hasn't connected to the Unity MCP in {CONNECT_S // 60} minutes: check the Editor "
                  "on your worktree, or say what's wrong.", sender="crewbots")

    def _unity_folder(self, task: Task) -> Path:
        folder = Path(task.worktree_path or task.workspace or "")
        root = (self.worktree_root or paths.worktrees()).resolve()
        if not (task.worktree_path or task.workspace) or not folder.resolve().is_relative_to(root):
            raise NotAllowed("crewbots opens Unity only on a worktree of the task's own; in the project's folder itself, "
                             "use the Editor the user has open")
        if not is_unity(folder):
            raise NotAllowed(f"{folder} isn't a Unity project")
        return folder.resolve()

    def _unity_mcp_url(self, task: Task) -> str | None:
        bot = self.bot_for(task.bot)
        return next((s.url for name, s in (bot.mcp if bot else {}).items() if "unity" in name.lower() and s.url), None)

    def _crew_editors(self) -> dict[Path, int]:
        """The Editors open on a worktree, by its folder: only crewbots opens those."""
        root = (self.worktree_root or paths.worktrees()).resolve()
        return {folder: pid for folder, pid in unity.editors().items() if folder.is_relative_to(root)}

    def _editor_in_use(self, folder: Path) -> bool:
        """Whether the work in `folder` or a review of it is running, or was in the last EDITOR_IDLE_S."""
        now = datetime.now(UTC)
        return any(t.id in self._runs or t.active
                   or (now - datetime.fromisoformat(t.updated_at)).total_seconds() < EDITOR_IDLE_S
                   for t in self.store.tasks_in(str(folder)))

    def close_editor(self, folder: Path) -> None:
        """Before a worktree goes: its Editor, if one is open on it."""
        if pid := self._crew_editors().get(folder.resolve()):
            unity.close_editor(pid)
            self._editor_room()

    def close_idle_editors(self) -> None:
        """On the scheduler's tick: Editors no task has used for EDITOR_IDLE_S."""
        for folder, pid in self._crew_editors().items():
            if not self._editor_in_use(folder):
                unity.close_editor(pid)
        self._editor_room()

    def _editor_room(self) -> None:
        if self._editor_waits and len(self._crew_editors()) < MAX_EDITORS:
            task_id = self._editor_waits.pop(0)
            try:
                self.send(task_id, "There's room for a Unity Editor now: ask for yours with open_unity_editor.",
                          sender="crewbots")
            except NotAllowed:
                pass
