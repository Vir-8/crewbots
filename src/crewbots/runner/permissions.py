"""Asking the user's leave. Work that needs what its bot's role card doesn't give it (to write in a folder outside its
task's own, or to reach the network), or the user's go-ahead before something big or hard to undo, is asked for with
`ask_permission` by whoever the user gave it to: the chief, for its own work or a teammate's, or a bot for work the
user gave it in its own chat. A bot working for a teammate escalates what it needs to them instead, who says go ahead
when the project's rules or the brief already allow it, and otherwise passes it up, so the user hears it from the
chief and only when it's theirs to give. The user allows it or not with a button: in the task's thread, and in the
chat it was asked from. What's allowed is the task's for the rest of it: its next turn opens with it
(`Sessions._session_for`).

Some things can't be asked for, and no answer changes them (backends/policy.py): the user's logins and keys,
crewbots' own files, the whole home folder, and what's the user's to do (pushing, merging, publishing).
"""

from pathlib import Path
from typing import Any

from crewbots import paths, prompts
from crewbots.backends.base import Access
from crewbots.backends.credentials import private_paths
from crewbots.crew import CrewError
from crewbots.store import Status, Task

KINDS = ("action", "folder", "network")


class Permissions:
    """The part of Runner for what bots ask the user to allow."""

    def ask_permission(self, task_id: str, what: str, why: str = "", kind: str = "action",
                       folder: str | None = None, for_task: str | None = None) -> dict[str, Any]:
        """The `ask_permission` tool: records what the chief asks, for its own task or `for_task`'s, shows it to the
        user, and has that task wait on the answer, as a question does."""
        asker = self.store.get_task(task_id)
        if not self.is_chief(asker.bot) and (for_task or not self._from_the_user(asker)):
            raise CrewError("a teammate gave you this work, so ask them: escalate what you need, and why, to whoever "
                            "gave it to you. Only the chief asks the user's leave for work a teammate gave out")
        task = self.store.get_task(for_task) if for_task else asker
        if task.status in (Status.CANCELLED, Status.FAILED):
            raise CrewError(f"{task.id} has ended: there's nothing to ask for")
        bot = self.crew.bots[task.bot]
        what, why = " ".join(str(what).split()), " ".join(str(why or "").split())
        if kind not in KINDS:
            raise CrewError(f"kind is one of {', '.join(KINDS)}")
        if not what:
            raise CrewError("say what you'd do, in a line the user can say yes to")
        granted = self._grants(task)
        target = ""
        if kind == "folder":
            path = _writable(folder)
            own = Path(task.worktree_path or task.workspace or self.crew.folder_of(bot)).expanduser().resolve()
            if bot.access is not Access.READ_ONLY and path.is_relative_to(own):
                raise CrewError("you can write there already: it's in your task's own folder")
            if any(path.is_relative_to(Path(g["folder"])) for g in granted if g["kind"] == "folder"):
                raise CrewError("you can write there already: the user allowed it")
            target = str(path)
        if kind == "network" and (bot.network is True or any(g["kind"] == "network" for g in granted)):
            raise CrewError("you have the network already")
        asked = self.store.ask_permission(task.id, kind, target, what, why)
        if task is asker or task.id in self._runs:
            self._escalated.add(task.id)  # it waits on the user once its turn ends (a teammate's waits already)
        if task.parent_id:
            self.store.update_task(task.id, reported=True)  # not a finished thread: the chat shows what it asks
        shown = {"permission": asked["id"], "kind": kind, "what": what, "why": why, "bot": asker.bot, "task": task.id,
                 **({"for": task.bot} if task is not asker else {}), **({"folder": target} if target else {})}
        where = self._shown_in(task) if task is asker else list(dict.fromkeys([task.id, self.chat(asker.bot).id]))
        for place in where:
            self._record(place, kind="permission", data=shown)
        self._refresh_working_state(task.bot)
        return asked

    def answer_permission(self, permission_id: int, allowed: bool) -> dict[str, Any]:
        """The user's answer, once: the task hears it and carries on, with what was allowed from its next turn."""
        asked = self.store.permission(permission_id)
        if asked["allowed"] is not None:
            return asked
        asked = self.store.answer_permission(permission_id, allowed)
        task = self.store.get_task(asked["task_id"])
        self._refresh_working_state(task.bot)
        # where it was asked: the chief's chat, for a teammate's
        for where in self.store.permission_shown_in(permission_id) or self._shown_in(task):
            self._record(where, kind="permission_answer", data={"permission": permission_id, "allowed": allowed})
        if task.status is not Status.CANCELLED:
            self.send(task.id, sender="crewbots", text=prompts.text(
                "permission-allowed" if allowed else "permission-refused", self.crew.home, what=asked["what"],
                grant=_grant(asked)))
        return asked

    def _from_the_user(self, task: Task) -> bool:
        """Whether the user gave this bot the work itself, in its chat with them or a thread they started for it, with no
        teammate in between (a group chat's work goes through its lead and the chief)."""
        chain = self._chain(task)
        return (chain[-1].kind in ("chat", "thread") and chain[-1].created_by == "user"
                and all(t.bot == task.bot for t in chain))

    def _shown_in(self, task: Task) -> list[str]:
        """Where what a task asks is shown: in its own thread, and in the chat (or the thread) its work started from."""
        root = self.store.root_of(task.id)
        return [task.id] if root.id == task.id else [task.id, root.id]


def _grant(asked: dict[str, Any]) -> str:
    match asked["kind"]:
        case "folder":
            return f"You can write in {asked['folder']} for the rest of this task. "
        case "network":
            return "You have the network for the rest of this task. "
    return ""


def _writable(folder: str | None) -> Path:
    """A folder the user may let a bot write in: one that's there, in their home folder (short of the whole of it),
    clear of their logins and keys, of crewbots' own folder, and of the apps' Library."""
    raw = str(folder or "").strip()
    if not raw:
        raise CrewError("say which folder, by its whole path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise CrewError("give the folder's whole path")
    path = path.resolve()
    home = paths.home().resolve()
    if path == home or not path.is_relative_to(home):
        raise CrewError("only a folder inside the home folder, and not the home folder itself")
    if not path.is_dir():
        raise CrewError(f"there's no folder at {path}: ask for one that's there (its parent, to make a new one)")
    own = paths.root().resolve()
    if path.is_relative_to(own) or path.is_relative_to(home / "Library"):
        raise CrewError("that's crewbots' own folder, or the apps' Library: not a bot's to write in")
    if any(p.resolve().is_relative_to(path) or path.is_relative_to(p.resolve()) for p in private_paths()):
        raise CrewError("it holds the user's logins or keys, which no bot writes or reads")
    return path
