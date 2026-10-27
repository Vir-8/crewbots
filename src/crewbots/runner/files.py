"""Files a bot shows the user, under what it says in a chat, a thread or a group chat: ones it shares (`share`).

A bot shows only files from its own folders: the task's worktree or workspace, its own workspace or folder in the
crew, the crew's attachments, and sessions' temp folders. A shared file is copied into the crew's attachments, so what
the user sees doesn't change when the bot's folder does.
"""

import asyncio
from pathlib import Path
from typing import Any

from crewbots import attachments, paths
from crewbots.backends import Access
from crewbots.runner.common import WORK, NotAllowed
from crewbots.store import Task

MAX_SHARED = 10  # files in one share


class Files:
    """The part of Runner that shows the user files its bots made."""

    async def share(self, task_id: str, files: list[str], text: str = "", room_id: str | None = None) -> dict[str, Any]:
        """The `share` tool: files from the bot's folders, copied into the crew's attachments and shown with `text`
        where it's working, or posted in a group chat (the one it's in, or `room_id`)."""
        task = self.store.get_task(task_id)
        if not files:
            raise attachments.AttachmentError("share needs at least one file")
        if len(files) > MAX_SHARED:
            raise attachments.AttachmentError(f"share at most {MAX_SHARED} files at once")
        room = self._room_to_post(task, room_id) if room_id or task.kind == "seat" else None
        sources = [self._own_file(task, f) for f in files]
        copied = await asyncio.to_thread(
            lambda: [attachments.copy_in(self.crew.home, s, attachments.SHARED_MAX_BYTES) for s in sources])
        if room is not None:
            return {"room_id": self.post_from(task_id, text, room["id"], attachments=copied)["room_id"], "files": copied}
        self._record(task_id, kind="shared", data={"text": text, "attachments": copied})
        return {"task_id": task_id, "files": copied}

    def _own_file(self, task: Task, given: str) -> Path:
        """A file the bot named, relative to where it works, if it's in one of its folders."""
        path = Path(given).expanduser()
        path = (path if path.is_absolute() else self._work_folder(task) / path).resolve()
        if not any(path.is_relative_to(folder) for folder in self._own_folders(task)):
            raise NotAllowed(f"{given} isn't in your folders: share files from where you work "
                             f"({self._work_folder(task)})")
        if not path.is_file():
            raise attachments.AttachmentError(f"{given} isn't a file")
        return path

    def _work_folder(self, task: Task) -> Path:
        """Where the task works, as `_cwd` decides, without making a worktree."""
        if task.worktree_path:
            return Path(task.worktree_path)
        bot = self.crew.bots.get(task.bot)
        workspace = task.workspace or (bot.workspace if bot else None)
        if workspace:
            return Path(workspace).expanduser()
        writes = bot is not None and bot.access is not Access.READ_ONLY and task.kind != "ask"
        return self.crew.home / WORK / task.bot if writes else self.crew.home

    def _own_folders(self, task: Task) -> list[Path]:
        bot = self.crew.bots.get(task.bot)
        folders = [task.worktree_path, task.workspace, bot.workspace if bot else None, self.crew.home / WORK / task.bot,
                   self.crew.home / attachments.FOLDER, paths.temp()]
        return [Path(f).expanduser().resolve() for f in folders if f]
