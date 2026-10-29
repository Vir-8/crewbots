"""User-approved pushes, kept separate from local merge proposals."""

import asyncio
import hashlib
import json
import os
import re
import signal
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from crewbots import attribution, ci, paths
from crewbots.runner.common import NotAllowed

REMOTE_TIMEOUT = 15
PUSH_TIMEOUT = 30
URL_USERINFO = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^\s'\"<>]*@")


class GitFailed(NotAllowed):
    """A Git command exited unsuccessfully, distinct from a timeout."""


class SnapshotError(NotAllowed):
    def __init__(self, reason: str, branch: str, upstream: str, push_url_display: str = ""):
        super().__init__(reason)
        self.branch = branch
        self.upstream = upstream
        self.push_url_display = push_url_display


def _git(folder: Path, *args: str, timeout: int = 5, pushing: bool = False) -> str:
    try:
        process = subprocess.Popen([paths.git(), "-C", str(folder), *args], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, errors="replace", stdin=subprocess.DEVNULL,
                                   env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                                   start_new_session=os.name != "nt")
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                for pipe in (process.stdout, process.stderr):
                    if pipe:
                        pipe.close()
            raise
    except subprocess.TimeoutExpired:
        detail = " The push may have completed; check the remote before retrying." if pushing else ""
        raise NotAllowed(f"Git timed out.{detail}") from None
    except OSError as e:
        raise NotAllowed(f"Git failed: {e}") from None
    if process.returncode:
        reason = URL_USERINFO.sub(r"\1***@", stderr + stdout)
        raise GitFailed(reason.strip() or "Git failed")
    return stdout.strip()


def snapshot(folder: Path) -> dict[str, Any]:
    branch = _git(folder, "symbolic-ref", "--quiet", "--short", "HEAD")
    tracking = _git(folder, "for-each-ref", "--format=%(upstream)", f"refs/heads/{branch}")
    if not tracking:
        raise SnapshotError("No upstream is configured for this branch.", branch, "(none)")
    upstream = _git(folder, "for-each-ref", "--format=%(upstream:short)", f"refs/heads/{branch}")
    try:
        return _destination_snapshot(folder, branch, upstream, tracking)
    except NotAllowed as e:
        display = ""
        try:
            remote = _git(folder, "config", "--get", f"branch.{branch}.remote")
            urls = _git(folder, "remote", "get-url", "--push", "--all", remote).splitlines()
            if len(urls) == 1:
                display = _display_url(urls[0])
        except NotAllowed:
            pass
        raise SnapshotError(str(e), branch, upstream, display) from None


def _display_url(url: str) -> str:
    # Show only host and path. Userinfo, query and fragment can contain secrets.
    if "://" in url:
        parsed = urlsplit(url)
        return parsed.netloc.rsplit("@", 1)[-1] + parsed.path
    return url.rsplit("@", 1)[-1].split("?", 1)[0].split("#", 1)[0]


def _remote_tip(folder: Path, url: str, destination: str) -> str | None:
    lines = _git(folder, "ls-remote", "--refs", "--", url, destination, timeout=REMOTE_TIMEOUT).splitlines()
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[1] == destination:
            return parts[0]
    return None


def _refuse_ai_attribution(folder: Path, tip: str, head: str) -> None:
    """What leaves the machine carries no AI trailer: a commit a bot made itself on a task branch can have one."""
    bad = []
    for entry in _git(folder, "log", "--format=%h %s%x00%B%x01", f"{tip}..{head}").split("\x01"):
        subject, _, message = entry.strip().partition("\x00")
        try:
            attribution.check(message)
        except attribution.AttributionError:
            bad.append(subject)
    if bad:
        raise NotAllowed("These commits carry an AI co-author trailer and would be pushed: " + "; ".join(bad))


def _destination_snapshot(folder: Path, branch: str, upstream: str, tracking: str) -> dict[str, Any]:
    remote = _git(folder, "config", "--get", f"branch.{branch}.remote")
    destination = _git(folder, "config", "--get", f"branch.{branch}.merge")
    if remote == "." or not destination.startswith("refs/heads/") or not tracking.startswith("refs/remotes/"):
        raise NotAllowed("The upstream must be a remote branch.")
    urls = _git(folder, "remote", "get-url", "--push", "--all", remote).splitlines()
    if len(urls) != 1:
        raise NotAllowed("The upstream must have exactly one push URL.")
    url_hash = hashlib.sha256(urls[0].encode()).hexdigest()
    head = _git(folder, "rev-parse", "HEAD")
    tip = _remote_tip(folder, urls[0], destination)
    if tip is None:
        raise NotAllowed("The destination branch does not exist on the remote.")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", tip):
        raise NotAllowed("Git returned an invalid remote tip.")
    try:
        _git(folder, "cat-file", "-e", f"{tip}^{{commit}}")
        _git(folder, "merge-base", "--is-ancestor", tip, head)
    except GitFailed:
        raise NotAllowed("The remote has commits this folder doesn't; not a fast-forward.") from None
    commits = _git(folder, "log", "--format=%H %s", f"{tip}..{head}").splitlines()
    _refuse_ai_attribution(folder, tip, head)
    return {"branch": branch, "upstream": upstream, "tracking": tracking, "remote": remote,
            "destination": destination, "push_url_display": _display_url(urls[0]), "url_hash": url_hash,
            "head": head, "tip": tip, "commits": commits}


SAME = ("branch", "destination", "url_hash", "head", "tip", "ci_commands")  # what makes two cards the same push


async def propose(runner: Any, task_id: str, project: str, summary: str) -> int:
    """A Push card for the user, one per project at a time: the same push again is the open card, and a different one
    replaces it. Proposals for a project are made one after another, so two at once can't both make a card."""
    task = runner.store.get_task(task_id)
    chief = runner.coordinator()
    if chief is None or task.bot != chief.name:
        raise NotAllowed("only the chief can propose a push")
    if not summary.strip():
        raise NotAllowed("a push proposal needs a summary")
    folder = runner.crew.project(project).folder.resolve()
    data: dict[str, Any] = {"project": project, "summary": summary.strip(), "folder": str(folder), "by": chief.name,
                            "ci_commands": list(runner.crew.project(project).ci)}  # a commit's own workflow goes with it
    chat = runner.chat(chief.name)
    async with runner.__dict__.setdefault("_push_locks", {}).setdefault(project, asyncio.Lock()):
        try:
            data.update(await asyncio.to_thread(snapshot, folder))
        except SnapshotError as e:
            data.update({"branch": e.branch, "upstream": e.upstream, "push_url_display": e.push_url_display,
                         "error": str(e)})
        except NotAllowed as e:
            data["error"] = str(e)
        waiting = open_card(runner, chat.id, project)
        if waiting and "error" not in data and all(waiting[1].get(k) == data.get(k) for k in SAME):
            return waiting[0]  # that push is on the user's screen already, its CI passed
        if "error" not in data:  # the card is only made for a commit CI passes on, or has none to run
            data["ci"] = await asyncio.to_thread(ci.check, folder, data["head"],
                                                 data["destination"].removeprefix("refs/heads/"),
                                                 runner.crew.project(project).ci)
        event = runner.store.append_event(chat.id, kind="push_proposed", data=data)
        runner.bus.publish({"type": "event", "event": event.to_json()})
        if waiting and "error" not in data:  # the newer card is the one to push
            replaced = runner.store.append_event(chat.id, kind="push_replaced",
                                                 data={"proposal": waiting[0], "by": event.id})
            runner.bus.publish({"type": "event", "event": replaced.to_json()})
        return event.id


def open_card(runner: Any, chat_id: str, project: str) -> tuple[int, dict[str, Any]] | None:
    """The project's Push card still waiting for the user: made without an error, not pushed, not replaced."""
    row = runner.store.db.execute(
        "select p.id, p.data from events p where p.task_id = ? and p.kind = 'push_proposed' "
        "and json_extract(p.data, '$.project') = ? and json_extract(p.data, '$.error') is null "
        "and not exists (select 1 from events e where e.task_id = p.task_id and e.kind in "
        "('push_started', 'push_result', 'push_replaced') and json_extract(e.data, '$.proposal') = p.id) "
        "order by p.id desc limit 1", (chat_id, project)).fetchone()
    return (row["id"], json.loads(row["data"])) if row else None


def proposal_message(runner: Any, proposal_id: int) -> str:
    row = runner.store.db.execute("select data from events where id = ? and kind = 'push_proposed'",
                                  (proposal_id,)).fetchone()
    data = json.loads(row["data"])
    if data.get("error"):
        return f"Push proposal {proposal_id} could not be made: {data['error']}"
    return (f"Push proposal {proposal_id} is waiting for the user. A project has one card at a time: proposing the "
            "same push again changes nothing, and a different one replaces it.")


async def perform(runner: Any, proposal_id: int) -> dict[str, Any]:
    row = runner.store.db.execute("select task_id, data from events where id = ? and kind = 'push_proposed'",
                                  (proposal_id,)).fetchone()
    if row is None:
        raise NotAllowed("no such push proposal")
    task_id, data = row["task_id"], json.loads(row["data"])
    previous = runner.store.db.execute("select kind from events where kind in ('push_started', 'push_result', "
                                       "'push_replaced') and json_extract(data, '$.proposal') = ? order by id limit 1",
                                       (proposal_id,)).fetchone()
    if previous and previous["kind"] == "push_replaced":
        raise NotAllowed("A newer Push card replaced this one; push from that.")
    if previous:
        raise NotAllowed("This push was already started; check the remote and make a new proposal.")
    runner._record(task_id, kind="push_started", data={"proposal": proposal_id})
    try:
        result = await asyncio.to_thread(_attempt, runner, proposal_id, data)
    except Exception as e:
        result = {"proposal": proposal_id, "ok": False, "reason": URL_USERINFO.sub(r"\1***@", str(e))}
    runner._record(task_id, kind="push_result", data=result)
    description = "completed" if result["ok"] else "failed"
    runner.send(task_id, f"Push of {data['project']} {description}: {result['reason']}", sender="crewbots")
    return result


def _attempt(runner: Any, proposal_id: int, data: dict[str, Any]) -> dict[str, Any]:
    try:
        if data.get("error"):
            raise NotAllowed(data["error"])
        project = runner.crew.project(data["project"])
        keys = ("branch", "upstream", "tracking", "remote", "destination", "push_url_display", "url_hash",
                "head", "tip", "commits")
        if str(project.folder.resolve()) != data["folder"] or snapshot(project.folder) != {key: data[key] for key in keys}:
            raise NotAllowed("The branch, upstream or commits changed since this card was made. Propose a new push.")
        if not data["commits"]:
            return {"proposal": proposal_id, "ok": True, "pushed": False,
                    "reason": "Everything up-to-date. Nothing was pushed."}
        url = _git(project.folder, "remote", "get-url", "--push", "--all", data["remote"])
        if hashlib.sha256(url.encode()).hexdigest() != data["url_hash"]:
            raise NotAllowed("The upstream changed since this card was made. Propose a new push.")
        try:
            tracking_old = _git(project.folder, "rev-parse", "--verify", data["tracking"])
        except GitFailed:
            tracking_old = None
        try:
            output = _git(project.folder, "-c", "core.hooksPath=/dev/null", "push", "--porcelain", "--no-verify",
                 f"--force-with-lease={data['destination']}:{data['tip']}",
                 "--no-follow-tags", "--recurse-submodules=no", "--", url,
                 f"{data['head']}:{data['destination']}", timeout=PUSH_TIMEOUT, pushing=True)
            if any(line.startswith("=\t") for line in output.splitlines()):
                raise NotAllowed("The remote changed; make a new proposal.")
        except GitFailed:
            if _remote_tip(project.folder, url, data["destination"]) != data["tip"]:
                raise NotAllowed("The remote changed; make a new proposal.") from None
            raise
        try:
            if tracking_old is not None:
                _git(project.folder, "-c", "core.hooksPath=/dev/null", "update-ref", data["tracking"],
                     data["head"], tracking_old)
            reason = "Push completed."
        except Exception as e:
            reason = f"Push completed, but the local tracking ref was not updated: {e}"
        return {"proposal": proposal_id, "ok": True, "pushed": True, "reason": reason}
    except Exception as e:
        return {"proposal": proposal_id, "ok": False, "reason": URL_USERINFO.sub(r"\1***@", str(e))}
