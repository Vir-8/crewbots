"""The `crewbots` command.

`init` and `serve` set up and run a crew. `task`, `say`, `watch` and the rest talk to the running daemon. `run` skips
the crew and the daemon altogether: one prompt through one backend, for trying backends out.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from crewbots import api, crew, typescript, worktree
from crewbots import routines as routines_module
from crewbots import service as svc
from crewbots.backends import BACKENDS, Access, RunSpec
from crewbots.events import Done, Event, Notice, SessionStarted, Text, ToolCall, ToolResult, Usage, from_json

PREVIEW_CHARS = 200
POLL_S = 0.5
ACTIVE = {"queued", "running"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crewbots")
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("CREWBOTS_HOME", crew.DEFAULT_HOME)),
                        help="the crew's folder (default: $CREWBOTS_HOME or ~/.crewbots/crew)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create a starter crew: a chief of staff, an engineer, a reviewer and a critic")
    serve = sub.add_parser("serve", help="run the daemon")
    serve.add_argument("--port", type=int, default=api.DEFAULT_PORT)
    serve.add_argument("--parallel", type=int, default=4, help="runs at the same time, across all bots")
    serve.add_argument("--no-sessions", action="store_true", help="start a fresh CLI for every turn instead of keeping one")

    sub.add_parser("bots", help="list the crew")
    task = sub.add_parser("task", help="give a bot a task, and follow it")
    task.add_argument("bot")
    task.add_argument("goal")
    task.add_argument("--workspace", type=Path, help="the folder or repo to work in, instead of the bot's own")
    task.add_argument("--no-watch", action="store_true")
    tasks = sub.add_parser("tasks", help="list tasks")
    tasks.add_argument("--status")
    tasks.add_argument("--bot")
    watch = sub.add_parser("watch", help="follow a task, and everything it delegates")
    watch.add_argument("task_id")
    say = sub.add_parser("say", help="send a task a follow-up message, and follow it")
    say.add_argument("task_id")
    say.add_argument("text")
    say.add_argument("--no-watch", action="store_true")
    cancel = sub.add_parser("cancel", help="stop a task")
    cancel.add_argument("task_id")
    diff = sub.add_parser("diff", help="what a task changed in its worktree")
    diff.add_argument("task_id")
    review = sub.add_parser("review", help="where a task's work stands in review")
    review.add_argument("task_id")
    merge = sub.add_parser("merge", help="merge a task's approved worktree into your checkout's current branch")
    merge.add_argument("task_id")
    merge.add_argument("--force", action="store_true", help="merge even though its current version is not approved")

    service = sub.add_parser("service", help="run the daemon as a login service (macOS launchd)")
    service.add_argument("action", choices=["install", "uninstall", "status", "start"])
    service.add_argument("--port", type=int, default=api.DEFAULT_PORT)
    service.add_argument("--label", default=svc.LABEL, help="the launchd label (another one for a second crew)")
    service.add_argument("--json", action="store_true")

    skills = sub.add_parser("skills", help="list the crew's skills, or add some")
    skills_sub = skills.add_subparsers(dest="skills_command")
    add = skills_sub.add_parser("add", help="copy in a skill folder, or a folder of skills such as pstack's skills/")
    add.add_argument("path", type=Path)
    add.add_argument("--name", help="the folder name in skills/; a pack's skills are named <name>:<skill>")

    routines = sub.add_parser("routines", help="list the crew's routines, run one now, or add a starter one")
    routines_sub = routines.add_subparsers(dest="routines_command")
    run_routine = routines_sub.add_parser("run", help="run a scheduled routine now")
    run_routine.add_argument("name")
    run_routine.add_argument("--no-watch", action="store_true")
    add_routine = routines_sub.add_parser("add", help="copy a starter routine into the crew (standup, retro, ...)")
    add_routine.add_argument("name", nargs="?")
    sub.add_parser("budgets", help="each bot's token use, against its budget")
    release = sub.add_parser("release", help="let a task held on a budget or plan limit go ahead")
    release.add_argument("task_id")
    sub.add_parser("reload", help="take up edited role cards and budgets without restarting the daemon")

    openapi = sub.add_parser("openapi", help="print the API's OpenAPI schema (what the app's types are generated from)")
    openapi.add_argument("--out", type=Path)
    openapi.add_argument("--ts", action="store_true", help="print TypeScript types instead of the schema")

    run = sub.add_parser("run", help="one prompt through one backend, without a crew or daemon")
    run.add_argument("prompt")
    run.add_argument("--backend", choices=sorted(BACKENDS), default="claude")
    run.add_argument("--cwd", type=Path, default=Path.cwd())
    run.add_argument("--access", choices=[a.value for a in Access], default=Access.READ_ONLY.value)
    run.add_argument("--model")
    run.add_argument("--resume", help="the backend session id to continue")
    run.add_argument("--worktree", action="store_true", help="run in a new git worktree of --cwd and print its diff")
    run.add_argument("--timeout", type=float)
    run.add_argument("--json", action="store_true", help="print raw events as JSON lines")

    args = parser.parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (crew.CrewError, routines_module.RoutineError, FileNotFoundError, RuntimeError) as e:
        print(f"crewbots: {e}", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as e:
        print(f"crewbots: {e.response.status_code} {e.response.json().get('detail', e.response.text)}", file=sys.stderr)
        return 1
    except httpx.ConnectError:
        print("crewbots: the daemon is not reachable (start it with `crewbots serve`)", file=sys.stderr)
        return 1


def _init(args: argparse.Namespace) -> int:
    home = crew.init(args.home)
    print(f"created a crew in {home}: {', '.join(crew.load(home).bots)}")
    print("edit the role cards in bots/ and the team files in team/, then run `crewbots serve`")
    return 0


def _serve(args: argparse.Namespace) -> int:
    api.serve(args.home, port=args.port, max_parallel=args.parallel, sessions=not args.no_sessions)
    return 0


def _bots(args: argparse.Namespace) -> int:
    for b in _client(args).get("/bots").raise_for_status().json():
        model = f" {b['model']}" if b["model"] else ""
        print(f"{b['name']:<14} {b['title']} · {b['backend']}{model} · {b['access']}\n{'':<14} {b['description']}")
    return 0


def _task(args: argparse.Namespace) -> int:
    client = _client(args)
    body: dict[str, Any] = {"bot": args.bot, "goal": args.goal}
    if args.workspace:
        body["workspace"] = str(args.workspace.expanduser().resolve())
    task = client.post("/tasks", json=body).raise_for_status().json()
    print(f"{task['id']} → {task['bot']}: {task['title']}", file=sys.stderr)
    return 0 if args.no_watch else _follow(client, task["id"])


def _tasks(args: argparse.Namespace) -> int:
    params = {k: v for k, v in {"status": args.status, "bot": args.bot}.items() if v}
    tasks = _client(args).get("/tasks", params={**params, "limit": 1000}).raise_for_status().json()
    ids = {t["id"] for t in tasks}
    children: dict[str, list[dict[str, Any]]] = {}
    for t in reversed(tasks):  # oldest first, so delegated tasks list in the order they were given
        children.setdefault(t["parent_id"] if t["parent_id"] in ids else None, []).append(t)

    def show(task: dict[str, Any], depth: int) -> None:
        indent = "  " * depth + ("└ " if depth else "")
        mark = {"proposed": "  [ready to merge]", "merged": "  [merged]"}.get(task["merge_state"], "")
        print(f"{task['id']}  {task['status']:<11} {indent}{task['bot']:<12} {task['title']}{mark}")
        for child in children.get(task["id"], []):
            show(child, depth + 1)

    for task in reversed(children.get(None, [])):  # newest top-level task first
        show(task, 0)
    return 0


def _service(args: argparse.Namespace) -> int:
    match args.action:
        case "install":
            crew.load(args.home)  # refuse to install a service for a folder that isn't a crew
            state = svc.install(args.home, args.port, args.label)
        case "uninstall":
            state = svc.uninstall(args.label)
        case "start":
            svc.start(args.label)
            state = svc.status(args.label)
        case _:
            state = svc.status(args.label)
    if args.json:
        print(json.dumps({"label": state.label, "installed": state.installed, "running": state.running,
                          "pid": state.pid, "plist": str(state.plist)}))
    elif not state.installed:
        print(f"{state.label}: not installed")
    else:
        print(f"{state.label}: installed ({state.plist}), {'running, pid ' + str(state.pid) if state.running else 'not running'}")
        print(f"logs: {svc.LOGS / 'daemon.log'}")
    return 0


def _skills(args: argparse.Namespace) -> int:
    home = args.home.expanduser()
    if args.skills_command == "add":
        for name in crew.add_skills(home, args.path, args.name):
            print(f"added {name}")
        print("list them under skills: in a role card to give them to a bot")
        return 0
    loaded = crew.load(home)
    users = {name: [b.name for b in loaded.bots.values() if name in b.skills or "all" in b.skills]
             for name in loaded.skills}
    for skill in loaded.skills.values():
        used = ", ".join(users[skill.name]) or "no bot yet"
        print(f"{skill.name:<24} ({used}) {skill.description[:PREVIEW_CHARS]}")
    return 0


def _routines(args: argparse.Namespace) -> int:
    if args.routines_command == "add":
        if not args.name:
            for name, text in sorted(routines_module.starters().items()):
                starter = routines_module.parse(text, name, Path(name))
                print(f"{name:<18} {starter.when:<40} {starter.description}")
            return 0
        path = routines_module.add_starter(args.home, args.name)
        print(f"added {path}; edit it to change when it runs or what it does")
        return 0
    client = _client(args)
    if args.routines_command == "run":
        task = client.post(f"/routines/{args.name}/run").raise_for_status().json()
        print(f"{task['id']} → {task['bot']}: {task['title']}")
        return 0 if args.no_watch else _follow(client, task["id"])
    listing = client.get("/routines").raise_for_status().json()
    for r in listing["routines"]:
        state = "" if r["enabled"] else " (off)"
        upcoming = f"  next {r['next_run_at'][:16].replace('T', ' ')}" if r["next_run_at"] and r["enabled"] else ""
        last = f", last {r['last_run_at'][:16].replace('T', ' ')} ({r['last_task_id']})" if r["last_run_at"] else ""
        print(f"{r['name']:<18} {r['bot']:<10} {r['when']}{upcoming}{last}{state}")
    for name, error in listing["errors"].items():
        print(f"{name}: couldn't be read: {error}", file=sys.stderr)
    if listing["github_error"]:
        print(f"GitHub: {listing['github_error']}", file=sys.stderr)
    if not listing["routines"] and not listing["errors"]:
        print("no routines; `crewbots routines add` lists the starter ones")
    return 0


def _budgets(args: argparse.Namespace) -> int:
    for b in _client(args).get("/budgets").raise_for_status().json():
        limit = f"of {b['tokens']:,}" if b["tokens"] else "(no budget)"
        held = f", holding {', '.join(b['held'])}" if b["held"] else ""
        print(f"{b['bot']:<12} {b['used']:>12,} {limit} in the last {b['per']}{held}")
    state = _client(args).get("/crew").raise_for_status().json()
    for limit in state["limits"]:
        used = f", {limit['used_percent']:g}% used" if limit["used_percent"] is not None else ""
        print(f"{limit['plan']} plan, {limit['window'].replace('_', ' ')} limit: {limit['status']}{used}")
    return 0


def _release(args: argparse.Namespace) -> int:
    task = _client(args).post(f"/tasks/{args.task_id}/release").raise_for_status().json()
    print(f"{task['id']}: released; it goes ahead past its budgets")
    return 0


def _reload(args: argparse.Namespace) -> int:
    _client(args).post("/crew/reload").raise_for_status()
    print("reloaded the crew's role cards")
    return 0


def _watch(args: argparse.Namespace) -> int:
    return _follow(_client(args), args.task_id)


def _say(args: argparse.Namespace) -> int:
    client = _client(args)
    after = _last_event_id(client, args.task_id)
    client.post(f"/tasks/{args.task_id}/messages", json={"text": args.text}).raise_for_status()
    return 0 if args.no_watch else _follow(client, args.task_id, after=after)


def _cancel(args: argparse.Namespace) -> int:
    task = _client(args).post(f"/tasks/{args.task_id}/cancel").raise_for_status().json()
    print(f"{task['id']} {task['status']}")
    return 0


def _diff(args: argparse.Namespace) -> int:
    diff = _client(args).get(f"/tasks/{args.task_id}/changes").raise_for_status().json()["diff"]
    print("(this task has no worktree)" if diff is None else diff or "(no changes)")
    return 0


def _review(args: argparse.Namespace) -> int:
    state = _client(args).get(f"/tasks/{args.task_id}/review").raise_for_status().json()
    if not state["round"]:
        print(f"{args.task_id} has not been reviewed")
        return 0
    verdict = ("approved" if state["approved"] else "out of rounds" if state["out_of_rounds"]
               else "changes requested" if state["failed"] else "in progress")
    print(f"{args.task_id}: round {state['round']} of {state['max_rounds']}, {verdict}")
    for r in state["reviews"]:
        print(f"  {r['reviewer']:<12} {(r['verdict'] or 'no verdict').upper():<10} {r['summary'] or ''}")
        for f in r["findings"]:
            print(f"    - {f.get('location', '?')}: {f.get('problem', '')}")
    return 0


def _merge(args: argparse.Namespace) -> int:
    task = _client(args).post(f"/tasks/{args.task_id}/merge", json={"force": args.force}).raise_for_status().json()
    print(f"merged {task['id']} ({task['branch']}) as {task['merged_commit'][:12]}")
    return 0


def _openapi(args: argparse.Namespace) -> int:
    schema = api.openapi_schema()
    text = typescript.from_openapi(schema) if args.ts else json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text)
    else:
        sys.stdout.write(text)
    return 0


def _run(args: argparse.Namespace) -> int:
    return asyncio.run(_run_direct(args))


async def _run_direct(args: argparse.Namespace) -> int:
    cwd = args.cwd.resolve()
    wt = None
    if args.worktree:
        wt = worktree.create(cwd, f"run-{uuid.uuid4().hex[:8]}")
        cwd = wt.path
        print(f"worktree {wt.path} on branch {wt.branch}", file=sys.stderr)
    spec = RunSpec(
        prompt=args.prompt, cwd=cwd, access=Access(args.access), model=args.model,
        resume_session_id=args.resume, timeout_s=args.timeout,
    )
    done: Done | None = None
    async for event in BACKENDS[args.backend]().run(spec):
        print(json.dumps({"kind": event.kind, **vars(event)}) if args.json else _format(event), flush=True)
        if isinstance(event, Done):
            done = event
    if wt:
        diff = worktree.changes(wt)
        print(diff or "(no changes)")
    return 1 if done is None or done.is_error else 0


COMMANDS = {
    "init": _init, "serve": _serve, "bots": _bots, "task": _task, "tasks": _tasks, "watch": _watch, "say": _say,
    "cancel": _cancel, "diff": _diff, "review": _review, "merge": _merge, "skills": _skills, "service": _service,
    "openapi": _openapi, "run": _run, "routines": _routines, "budgets": _budgets, "release": _release,
    "reload": _reload,
}


def _client(args: argparse.Namespace) -> httpx.Client:
    info = api.read_daemon_info(args.home)
    return httpx.Client(base_url=f"{info.url}/v1", headers={"Authorization": f"Bearer {info.token}"}, timeout=30)


def _last_event_id(client: httpx.Client, task_id: str) -> int:
    after = 0
    while batch := client.get(f"/tasks/{task_id}/events", params={"after": after}).raise_for_status().json():
        after = batch[-1]["id"]
    return after


def _follow(client: httpx.Client, root_id: str, after: int = 0) -> int:
    """Prints the task's events, and those of every task it delegates, until none of them is running.

    Polling is plenty for a terminal; the app uses /live.
    """
    cursors = {root_id: after}
    while True:
        tasks = {t["id"]: t for t in client.get("/tasks", params={"limit": 1000}).raise_for_status().json()}
        grew = True
        while grew:  # the whole tree under the root, however deep
            new = [t for t, task in tasks.items() if task["parent_id"] in cursors and t not in cursors]
            cursors.update(dict.fromkeys(new, 0))
            grew = bool(new)
        events = []
        for task_id, cursor in cursors.items():
            batch = client.get(f"/tasks/{task_id}/events", params={"after": cursor}).raise_for_status().json()
            if batch:
                cursors[task_id] = batch[-1]["id"]
                events += batch
        for e in sorted(events, key=lambda e: e["id"]):
            prefix = "" if e["task_id"] == root_id else f"[{tasks[e['task_id']]['bot']} {e['task_id']}] "
            print(prefix + _format_stored(e), flush=True)
        if not events and not any(tasks[t]["status"] in ACTIVE for t in cursors if t in tasks):
            status = tasks[root_id]["status"]
            print(f"· {root_id} is {status}", file=sys.stderr)
            return 0 if status in ("idle", "needs_input") else 1
        if not events:
            time.sleep(POLL_S)


def _format_stored(e: dict[str, Any]) -> str:
    match e["kind"]:
        case "message":
            return f"\n» {e['data']['from']}: {e['data']['text']}"
        case "worktree":
            return f"· worktree {e['data']['path']} on {e['data']['branch']}"
        case "handoff":
            d = e["data"]
            return f"⇢ HANDOFF done: {d['done']} | output: {', '.join(d['output']) or '-'} | open: {d['open'] or '-'}"
        case "escalation":
            return f"? escalated: {e['data']['question']}"
        case "review":
            d = e["data"]
            return f"⚖ {d['reviewer']}: {d['verdict'].upper()} (version {d['version']}) {d['summary']}"
        case "merge_proposed":
            return f"⇢ proposed for merging by {e['data']['by']}: {e['data']['summary']}"
        case "merged":
            return f"✓ merged as {e['data']['commit'][:12]} into {e['data']['into']}"
        case "memory":
            d = e["data"]
            where = "the crew's notes" if d["scope"] == "crew" else "its notes"
            return f"✎ {'remembered' if d['action'] == 'remember' else 'forgot'} in {where}: {d['title']}"
        case "held":
            return f"⏸ held: {e['data']['reason']} (crewbots release {e['task_id']})"
        case "released":
            return "▶ released past its budgets"
        case "rate_limit":
            d = e["data"]
            used = f" at {d['used_percent']:g}%" if d.get("used_percent") is not None else ""
            return f"! the {d['plan']} plan's {d['window'].replace('_', ' ')} limit: {d['status']}{used}"
        case "rollover":
            return f"↻ continued in a fresh session; the conversation was {e['data']['percent']}% of its context"
    event = from_json(e["kind"], e["data"])
    return _format(event) if event else f"· {e['kind']} {json.dumps(e['data'])}"


def _format(event: Event) -> str:
    match event:
        case SessionStarted(session_id=sid):
            return f"· session {sid}"
        case Text(text=text):
            return text
        case ToolCall(name=name, input=inp):
            return f"→ {name} {_preview(json.dumps(inp))}"
        case ToolResult(output=out, is_error=err):
            return f"{'✗' if err else '←'} {_preview(out)}"
        case Usage(input_tokens=i, output_tokens=o, cached_input_tokens=c, cost_usd=cost):
            price = f", ${cost:.4f} notional" if cost is not None else ""
            return f"· {i} in ({c} cached), {o} out{price}"
        case Notice(message=m):
            return f"! {m}"
        case Done(is_error=True, error=e):
            return f"✗ failed: {e}"
        case Done():
            return "· done"
    return repr(event)


def _preview(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= PREVIEW_CHARS else s[:PREVIEW_CHARS] + "…"


if __name__ == "__main__":
    sys.exit(main())
