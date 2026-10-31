"""Files a bot shows the user, under what it says in a chat, a thread or a group chat: ones it shares (`share`), ones
it names in a reply, a post or what it tells the user (`tell_user`), and images its tools return (a file it looked at,
a screenshot from Blender, GIMP or Resolve), shown in its steps.

A bot shows only files from its own folders: the task's worktree or workspace, its own workspace or folder in the
crew, the crew's attachments, and sessions' temp folders. A shared file is copied into the crew's attachments, so what
the user sees doesn't change when the bot's folder does; a named one is shown where it is.
"""

import asyncio
import base64
import binascii
import mimetypes
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from crewbots import attachments, paths
from crewbots.backends.claude_cli import SANDBOX_TEMP
from crewbots.runner.common import NotAllowed
from crewbots.store import Task

MAX_SHARED = 10  # files in one share
MAX_TOOL_IMAGES = 8  # kept from one tool result
MAX_NAMED = 6  # files shown under one reply

# What a reply names that's worth showing: what a bot makes for the user (renders, recordings, documents, designs), not
# the code it changed, which the diff shows. A text file counts only if it changed in the turn that names it: a note
# or a README named in passing isn't news.
SHOWN = {"png", "jpg", "jpeg", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff", "svg", "mp4", "mov", "m4v", "webm",
         "mkv", "avi", "mp3", "m4a", "wav", "aac", "flac", "ogg", "aif", "aiff", "pdf", "docx", "xlsx", "pptx", "key",
         "pages", "numbers", "odt", "ods", "odp", "blend", "psd", "xcf", "kra", "glb", "gltf", "fbx", "obj", "usdz",
         "drp", "zip"}
WRITTEN = {"md", "txt", "csv", "tsv", "html", "htm", "json", "log"}
NAMED = re.compile(
    r"\]\(<([^>\n]+)>\)"  # a Markdown link's target, in <...> when it has spaces
    r"|\]\(((?:[^()\s]|\([^()\s]*\))+)\)"  # or without, with at most one level of (...) in it
    r"|`([^`\n]+)`"  # inline code
    r"|(?<![\w/.~-])((?:~|\.{1,2})?/?[\w.-]+(?:/[\w.-]+)*\.[A-Za-z0-9]{1,6})(?![\w/])"  # a bare path with an extension
)


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
        self._shared_now.setdefault(task_id, set()).update(sources)  # its reply needn't show them again
        if room is not None:
            return {"room_id": self.post_from(task_id, text, room["id"], attachments=copied)["room_id"], "files": copied}
        self._record(task_id, kind="shared", data={"text": text, "attachments": copied})
        return {"task_id": task_id, "files": copied}

    def _named(self, task: Task, text: str, shown: set[Path]) -> list[dict[str, Any]]:
        """Files a tool's text names (`post`, `tell_user`), found as a reply's are."""
        return self._mentioned(task, self._work_folder(task), text, self._turn_started.get(task.id, time.time()), shown)

    def _mentioned(self, task: Task, cwd: Path, text: str, since: float, shown: set[Path]) -> list[dict[str, Any]]:
        """Files a reply names that its bot made, in its own folders, to show under it where they are: all but those
        `shown` where it goes already."""
        if "." not in text:
            return []
        folders, found = self._own_folders(task), {}
        for match in NAMED.finditer(text):
            given = next(g for g in match.groups() if g).strip()
            if given.startswith("file://"):
                given = unquote(given.removeprefix("file://"))
            ext = given.rsplit(".", 1)[-1].lower() if "." in given else ""
            if ext not in SHOWN and ext not in WRITTEN or "://" in given:
                continue
            path = Path(given).expanduser()
            try:
                path = (path if path.is_absolute() else cwd / path).resolve()
                stat = path.stat()
            except (OSError, ValueError):
                continue
            if (path in found or path in shown or not path.is_file() or not stat.st_size
                    or not any(path.is_relative_to(f) for f in folders)
                    or (ext in WRITTEN and stat.st_mtime < since - 2)):
                continue
            found[path] = {**attachments.info(path), "id": ""}  # where it is, not a copy: Open opens the real file
            if len(found) == MAX_NAMED:
                break
        return list(found.values())

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
        if task.workspace or not bot:
            return Path(task.workspace).expanduser() if task.workspace else self.crew.home
        return self.crew.folder_of(bot)

    def _own_folders(self, task: Task) -> list[Path]:
        bot = self.crew.bots.get(task.bot)
        folders = [task.worktree_path, task.workspace, self.crew.folder_of(bot) if bot else None,
                   self.crew.home / attachments.FOLDER, paths.temp(), SANDBOX_TEMP]  # temp folders: Codex's, Claude's
        return [Path(f).expanduser().resolve() for f in folders if f]

    def _kept_images(self, images: list[dict[str, Any]], read: str | None = None) -> list[dict[str, Any]]:
        """A tool result's images, kept in the crew's attachments (each once, by what's in it): what the result
        stores in their place, named after the file the tool read, if it read one. One that can't be read or is too
        big is left out."""
        kept = []
        for image in images[:MAX_TOOL_IMAGES]:
            try:
                if path := image.get("path"):
                    source = Path(path).expanduser()
                    if source.stat().st_size > attachments.TOOL_IMAGE_BYTES:
                        continue
                    data, name = source.read_bytes(), source.name
                else:
                    data = base64.b64decode(image["data"], validate=True)  # in the format the tool sent, whatever it read
                    name = (Path(read).stem if read else "image") + (mimetypes.guess_extension(image.get("mime") or "") or ".png")
                if len(data) <= attachments.TOOL_IMAGE_BYTES:
                    kept.append(attachments.keep(self.crew.home, data, name))
            except (OSError, KeyError, binascii.Error):
                continue
        return kept
