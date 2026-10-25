# crewbots

A crew of persistent AI agents that you manage instead of babysit. You give a goal to a chief of staff, which splits it into tasks with one owner each and hands them to specialist bots: an engineer, a reviewer, or whatever you add, such as a video editor working in DaVinci Resolve. The bots run in parallel, each writing bot in its own git worktree, and you hear back when there is something to review or decide.

The agents run on the official **Claude Code** and **Codex** CLIs in non-interactive mode, so they use the Claude and ChatGPT plans you already have. crewbots is the layer above them: bots, tasks, worktrees, delegation, rules and the app. The plan is in [docs/DESIGN.md](docs/DESIGN.md).

## How it works

- **A crew is a folder.** `bots/` has one role card per bot, and `team/` has the files every bot reads (mission, standards, protocol, rules).
- **A role card** is Markdown with frontmatter:
  - The frontmatter is what the harness enforces: backend, model, access level, workspace, and the bot's own MCP servers.
  - The body is the card itself: its mission, what it owns, what it does *not* own, and what done means.
  - See [docs/examples/video-editor.md](docs/examples/video-editor.md).
- **A task** is the unit of work and of conversation:
  - Each task has exactly one owner bot and its own backend session, so a follow-up message picks up where the bot left off.
  - A task by a writing bot in a git repo gets its own worktree, on the branch `crewbots/<task id>`.
  - Every event is stored, so you can read the whole transcript later.
- **The daemon** runs the tasks and serves an HTTP and WebSocket API on localhost. The desktop app and the `crewbots` command both use that API.
  - Every request needs the daemon's token, which is written to `daemon.json` and readable only by you.
  - The daemon also checks the Host header, so no web page you have open can drive your bots.
- **Backends** translate each CLI's JSON output into one shared event stream.
  - Bots get crewbots' MCP servers and their own, never your personal ones.
  - `ANTHROPIC_API_KEY` is hidden from Claude by default, so a run stays on your subscription.

## Try it

You need `claude` and `codex` installed and signed in.

```bash
uv sync
```

```bash
uv run crewbots init
```

```bash
uv run crewbots serve
```

`init` creates the starter crew (chief, engineer, reviewer) in `~/.crewbots/crew`. Edit its role cards and team files to make it yours. In another terminal:

```bash
uv run crewbots task engineer "Add a --verbose flag to the CLI, with a test" --workspace ~/code/myproject
```

```bash
uv run crewbots say <task id> "Also document it in the README"
```

```bash
uv run crewbots diff <task id>
```

`crewbots tasks` lists everything, `watch` follows a task, and `cancel` stops one. Tasks still running when the daemon stops are marked interrupted; send them a message to carry on. `crewbots run` skips the crew and the daemon entirely and sends one prompt through one backend.

## Tests

```bash
uv run pytest -q
```

No test calls a model. Backend tests replay real CLI output (`tests/fixtures/`) through a fake CLI. Runner and API tests use an in-process fake backend.

## Layout

```
src/crewbots/
  events.py          the shared event stream
  backends/          claude_cli, codex_cli, and the subprocess runner they share
  crew.py            role cards and team files → bots and their instructions
  store.py           tasks and events, in SQLite
  runner.py          runs tasks: sessions, worktrees, queued follow-ups, the parallel limit
  api.py             the daemon: HTTP + WebSocket, token and host checks
  worktree.py        one git worktree per task
  cli.py             the `crewbots` command
  templates/crew/    the starter crew
tests/               one file per module, plus the fakes and fixtures
docs/                the design, and example role cards
```
