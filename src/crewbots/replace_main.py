"""One-off replacement of the checked-out branch by a rewritten history with an identical tree.

The chief proposes it and the user's button on the card performs it. Nothing else is touched: no other ref but a backup
of the old tip, no working file, no remote. Commits on the old branch stay reachable from the backup ref, so merge
records that name them (merged_commit, merged_tip, a task's base) keep resolving. Backups are only a recovery point:
nothing reads them to decide what is merged, and merged_commit is left as it is. "Already on main" and the sweep use
the records, tree and merge-tree checks, never main's ancestry. Older merge records have no merged_tip and fall back to
merged_commit^2, so before the swap that second parent is saved as merged_tip (see _backfill_tips). A branch merged by
hand outside crewbots has no record and is not covered.
"""

import asyncio
import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from crewbots import ci
from crewbots.push import REMOTE_TIMEOUT, GitFailed, _display_url, _git, _remote_tip
from crewbots.runner.common import NotAllowed

BACKUP = "refs/backup/"
IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "SQUASH_MSG", "rebase-merge", "rebase-apply", "index.lock")
KEYS = ("project", "folder", "target", "branch", "old", "new", "tree", "upstream", "remote_tip", "url_hash", "backup")


def _blocking(runner: Any, folder: Path) -> list[str]:
    """Tasks of the project whose merge is proposed or under way: their already-on checks would run against the
    rewritten branch."""
    rows = runner.store.db.execute("select id, merge_state from tasks where repo = ? and merge_state in "
                                   "('proposed', 'merging') order by id", (str(folder),)).fetchall()
    return [f"{row['id']} ({row['merge_state']})" for row in rows]


def _remote(folder: Path, target: str) -> dict[str, str]:
    """The live tip of the branch a push of `target` goes to, read from the real push URL. Raises NotAllowed for
    anything that can't be established; there is no fallback to a local ref."""
    upstream = _git(folder, "for-each-ref", "--format=%(upstream:short)", f"refs/heads/{target}")
    if not upstream:
        raise NotAllowed(f"{target} has no upstream")
    remote = _git(folder, "config", "--get", f"branch.{target}.remote")
    destination = _git(folder, "config", "--get", f"branch.{target}.merge")
    if remote == "." or not destination.startswith("refs/heads/"):
        raise NotAllowed("the upstream must be a remote branch")
    urls = _git(folder, "remote", "get-url", "--push", "--all", remote).splitlines()
    if len(urls) != 1:
        raise NotAllowed("the upstream must have exactly one push URL")
    tip = _remote_tip(folder, urls[0], destination)
    if tip is None or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", tip):
        raise NotAllowed("the destination branch does not exist on the remote")
    try:
        symref = _git(folder, "ls-remote", "--symref", "--", urls[0], "HEAD", timeout=REMOTE_TIMEOUT).splitlines()
    except NotAllowed as e:
        raise NotAllowed(f"the remote's default branch could not be read ({e})") from None
    default = next((line.split()[1] for line in symref if line.startswith("ref: refs/heads/")), "")
    if not default:
        raise NotAllowed("the remote did not say which branch is its default")
    return {"upstream": upstream, "destination": destination, "default": default, "remote_tip": tip,
            "push_url": _display_url(urls[0]),
            "url_hash": hashlib.sha256(urls[0].encode()).hexdigest()}


def _backup_name(folder: Path, target: str) -> str:
    stem = f"{BACKUP}{target}-{datetime.date.today().isoformat()}"
    name, n = stem, 1
    while _exists(folder, name):
        n += 1
        name = f"{stem}-{n}"
    return name


def _exists(folder: Path, ref: str) -> bool:
    try:
        _git(folder, "rev-parse", "--verify", "--quiet", ref)
        return True
    except GitFailed:
        return False


def snapshot(folder: Path, branch: str, blocking: list[str] | None = None) -> dict[str, Any]:
    """What replacing the checked-out branch with `branch` would do, and whether each guard holds. `error` is the first
    guard that doesn't; the other fields are filled in as far as the guards allowed."""
    checks: list[dict[str, Any]] = []
    data: dict[str, Any] = {"branch": branch, "checks": checks}

    def check(name: str, ok: bool, detail: str) -> bool:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            data.setdefault("error", f"{name}: {detail}")
        return ok

    try:
        target = _git(folder, "symbolic-ref", "--quiet", "--short", "HEAD")
    except GitFailed:
        target = ""
    data["target"] = target
    if not check("The checked-out branch is the target", bool(target) and target != branch,
                 f"{target} is checked out" if target and target != branch else
                 "a branch must be checked out, and not the replacement itself"):
        return data
    try:
        new = _git(folder, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
    except GitFailed:
        check("The replacement branch exists", False, f"there is no local branch {branch}")
        return data
    old = _git(folder, "rev-parse", f"refs/heads/{target}")
    tree = _git(folder, "rev-parse", f"{old}^{{tree}}")
    data |= {"old": old, "new": new, "tree": tree}
    same = tree == _git(folder, "rev-parse", f"{new}^{{tree}}") and old != new
    check("Same tree", same, "the trees are identical" if same else "the trees differ, or the branches are the same commit")

    try:
        remote = _remote(folder, target)
    except NotAllowed as e:
        check("The target is the pushed branch", False, f"the live remote could not be read: {e}")
        return data
    data |= {k: remote[k] for k in ("upstream", "remote_tip", "url_hash", "push_url")}
    if not check("The target is the pushed branch", remote["destination"] == f"refs/heads/{target}",
                 f"{target} pushes to {remote['push_url']} {remote['destination']}"):
        return data
    if not check("The target is the remote's default branch", remote["destination"] == remote["default"],
                 f"the default is {remote['default'].removeprefix('refs/heads/')}, the target is {target}"):
        return data
    tip = remote["remote_tip"]
    try:
        _git(folder, "cat-file", "-e", f"{tip}^{{commit}}")
        _git(folder, "merge-base", "--is-ancestor", tip, new)
        check("Pushed history is kept", True, f"the remote tip {tip[:12]} is an ancestor of {branch}")
    except GitFailed:
        check("Pushed history is kept", False, f"the remote tip {tip[:12]} is not an ancestor of {branch}, "
                                              "or is not in this repository (fetch first)")
        return data
    data |= {"old_commits": int(_git(folder, "rev-list", "--count", f"{tip}..{old}")),
             "new_commits": int(_git(folder, "rev-list", "--count", f"{tip}..{new}"))}

    paths = [Path(p) for p in (_git(folder, "rev-parse", "--git-path", name) for name in IN_PROGRESS)]
    underway = next((p.name for p in paths if (p if p.is_absolute() else folder / p).exists()), "")
    check("No merge in progress", not underway, f"{underway} is in the way" if underway else "none")
    data["blocking"] = blocking or []
    check("No open merge proposals", not blocking, ", ".join(blocking) if blocking else "none")
    clean = not _git(folder, "status", "--porcelain", timeout=60)
    check("Folder is clean", clean, "no uncommitted changes" if clean else "there are uncommitted changes")
    data["backup"] = _backup_name(folder, target)
    return data


async def propose(runner: Any, task_id: str, project: str, branch: str, summary: str) -> int:
    task = runner.store.get_task(task_id)
    chief = runner.coordinator()
    if chief is None or task.bot != chief.name:
        raise NotAllowed("only the chief can propose replacing main")
    if not summary.strip():
        raise NotAllowed("a proposal needs a summary")
    folder = runner.crew.project(project).folder.resolve()
    data: dict[str, Any] = {"project": project, "summary": summary.strip(), "folder": str(folder), "by": chief.name}
    try:
        data.update(await asyncio.to_thread(snapshot, folder, branch, _blocking(runner, folder)))
    except NotAllowed as e:
        data |= {"branch": branch, "error": str(e)}
    if "error" not in data:  # the card is only made for a commit CI passes on, or has none to run
        data["ci"] = await asyncio.to_thread(ci.check, folder, data["new"], data["target"], runner.crew.project(project).ci)
    event = runner.store.append_event(runner.chat(chief.name).id, kind="replace_main_proposed", data=data)
    runner.bus.publish({"type": "event", "event": event.to_json()})
    return event.id


def proposal_message(runner: Any, proposal_id: int) -> str:
    row = runner.store.db.execute("select data from events where id = ? and kind = 'replace_main_proposed'",
                                  (proposal_id,)).fetchone()
    data = json.loads(row["data"])
    if data.get("error"):
        return f"Replace-main proposal {proposal_id} was refused: {data['error']}"
    return f"Replace-main proposal {proposal_id} is waiting for the user."


def perform(runner: Any, proposal_id: int) -> dict[str, Any]:
    """The button. Runs on the event loop, not a thread, so it cannot overlap a merge, which also runs there."""
    row = runner.store.db.execute("select task_id, data from events where id = ? and kind = 'replace_main_proposed'",
                                  (proposal_id,)).fetchone()
    if row is None:
        raise NotAllowed("no such proposal")
    task_id, data = row["task_id"], json.loads(row["data"])
    if runner.store.db.execute("select 1 from events where kind in ('replace_main_started', 'replace_main_result') and "
                               "json_extract(data, '$.proposal') = ?", (proposal_id,)).fetchone():
        raise NotAllowed("This replacement was already started; make a new proposal.")
    runner._record(task_id, kind="replace_main_started", data={"proposal": proposal_id})
    try:
        result = {"proposal": proposal_id} | _attempt(runner, data)
    except Exception as e:
        result = {"proposal": proposal_id, "ok": False, "reason": str(e)}
    runner._record(task_id, kind="replace_main_result", data=result)
    runner.send(task_id, f"Replacing {data.get('target')} in {data['project']} "
                         f"{'completed' if result['ok'] else 'failed'}: {result['reason']}", sender="crewbots")
    return result


def _backfill_tips(runner: Any, folder: Path) -> None:
    """Give merged tasks with no merged_tip the second parent of their merge commit. Idempotent and right whether or not
    the swap follows. Refuses, before any ref moves, if one cannot be written."""
    rows = runner.store.db.execute("select id, merged_commit from tasks where repo = ? and merge_state = 'merged' and "
                                   "merged_tip is null and merged_commit is not null", (str(folder),)).fetchall()
    for row in rows:
        try:
            parents = _git(folder, "rev-list", "--parents", "-n", "1", row["merged_commit"]).split()
            if len(parents) == 3:  # the commit, then two parents
                runner.store.update_task(row["id"], merged_tip=parents[2])
        except Exception as e:
            raise NotAllowed(f"could not save the merged tip of {row['id']}; nothing was changed ({e})") from None


def _attempt(runner: Any, data: dict[str, Any]) -> dict[str, Any]:
    if data.get("error"):
        raise NotAllowed(data["error"])
    folder = runner.crew.project(data["project"]).folder.resolve()
    now = snapshot(folder, data["branch"], _blocking(runner, folder))
    if now.get("error"):
        raise NotAllowed(now["error"])
    if str(folder) != data["folder"] or any(now[k] != data[k] for k in KEYS if k not in ("project", "folder")):
        raise NotAllowed("The branches or the backup name changed since this card was made. Propose again.")
    _backfill_tips(runner, folder)
    old, new, target, backup = data["old"], data["new"], data["target"], data["backup"]
    _git(folder, "update-ref", "-m", f"backup of {target} before replacing it", backup, old, "")  # must not exist yet
    try:
        _git(folder, "update-ref", "-m", f"replace {target} with {data['branch']}", f"refs/heads/{target}", new, old)
    except GitFailed as e:
        _git(folder, "update-ref", "-d", backup, old)
        raise NotAllowed(f"{target} moved; nothing was changed ({e})") from None
    reason = f"{target} now points at {new[:12]}; the old tip {old[:12]} is saved as {backup}."
    try:
        _git(folder, "update-index", "-q", "--refresh", timeout=60)
    except NotAllowed as e:
        reason += f" The index could not be refreshed ({e}); working files are unchanged."
    return {"ok": True, "reason": reason, "backup": backup, "old": old, "new": new}

