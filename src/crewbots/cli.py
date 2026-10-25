"""`crewbots run`: one prompt through one backend, with its events printed as they arrive. For trying backends out."""

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from crewbots import worktree
from crewbots.backends import BACKENDS, Access, RunSpec
from crewbots.events import Done, Event, Notice, SessionStarted, Text, ToolCall, ToolResult, Usage

PREVIEW_CHARS = 200


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crewbots")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one prompt through a backend")
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
    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
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
