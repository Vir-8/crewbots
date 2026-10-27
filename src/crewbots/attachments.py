"""Files and images attached to a message: ones you send a bot, and ones a bot shares with you (`share`).

Each is kept in the crew's folder, `attachments/<id>/<its name>`, and the message a bot gets ends with the list of them
and where they are, so it opens what it needs with its own tools: Claude Code reads images and PDFs itself, and Codex
views images. That works the same on both backends, in a chat, a thread or a group chat, and for a message that waits
behind a turn.
"""

import mimetypes
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

FOLDER = "attachments"
MAX_BYTES = 25_000_000
SHARED_MAX_BYTES = 200_000_000  # a bot's renders and recordings run bigger, and are copied, not sent through the API
ID = re.compile(r"[0-9a-f]{12}")


class AttachmentError(ValueError):
    pass


def save(home: Path, name: str, data: bytes) -> dict[str, Any]:
    """Keeps bytes sent from the app: a pasted image, or a dropped file."""
    if len(data) > MAX_BYTES:
        raise AttachmentError(f"{name} is over {MAX_BYTES // 1_000_000} MB")
    path = _new(home, name)
    path.write_bytes(data)
    return info(path)


def copy_in(home: Path, source: Path, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    """Keeps a copy of a file you picked, or one a bot shared, so later edits or a move don't change what was sent."""
    source = source.expanduser()
    if not source.is_file():
        raise AttachmentError(f"{source} isn't a file")
    if source.stat().st_size > max_bytes:
        raise AttachmentError(f"{source.name} is over {max_bytes // 1_000_000} MB")
    path = _new(home, source.name)
    shutil.copyfile(source, path)
    return info(path)


def find(home: Path, attachment_id: str) -> dict[str, Any]:
    folder = home / FOLDER / attachment_id
    if not ID.fullmatch(attachment_id) or not folder.is_dir():
        raise AttachmentError(f"no attachment {attachment_id}")
    files = [p for p in folder.iterdir() if p.is_file()]
    if not files:
        raise AttachmentError(f"attachment {attachment_id} is empty")
    return info(files[0])


def info(path: Path) -> dict[str, Any]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {"id": path.parent.name, "name": path.name, "path": str(path), "mime": mime, "size": path.stat().st_size,
            "kind": "image" if mime.startswith("image/") else "file"}


def listing(attachments: list[dict[str, Any]]) -> str:
    """What's added to the message a bot gets."""
    if not attachments:
        return ""
    lines = [f"- {a['name']} ({'image' if a['kind'] == 'image' else a['mime']}, {_size(a['size'])}): {a['path']}"
             for a in attachments]
    return ("\n\nAttached to this message (open them with your file tools as you need them; you can view images):\n"
            + "\n".join(lines))


def _new(home: Path, name: str) -> Path:
    safe = re.sub(r"[^\w.\- ]+", "_", Path(name).name).strip(" .") or "file"
    folder = home / FOLDER / uuid.uuid4().hex[:12]
    folder.mkdir(parents=True)
    return folder / safe


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(1, round(n / 1000))} KB"
