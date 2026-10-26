# crewbots

A crew of persistent AI agents that you manage instead of babysit. You give a goal to a chief of staff, which splits it into tasks with one owner each and hands them to specialist bots: an engineer, a reviewer, or whatever you add, such as a video editor working in DaVinci Resolve. The bots run in parallel, each writing bot in its own git worktree, and you hear back when there is something to review or decide.

The agents run on the official **Claude Code** and **Codex** CLIs in non-interactive mode, so they use the Claude and ChatGPT plans you already have. crewbots is the layer above them: bots, tasks, worktrees, delegation, rules and the app. The plan is in [docs/DESIGN.md](docs/DESIGN.md), and the architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How it works

- **A crew is a folder.** `bots/` has one role card per bot, `team/` has the files every bot reads (mission, standards, protocol, rules), and `skills/` has the skills bots can use.
- **A role card** is Markdown with frontmatter:
  - The frontmatter is what the harness enforces: backend, model, access level, workspace, and the bot's own MCP servers.
  - The body is the card itself: its mission, what it owns, what it does *not* own, and what done means.
  - See [docs/examples/video-editor.md](docs/examples/video-editor.md).
- **A task** is the unit of work and of conversation:
  - Each task has exactly one owner bot and its own backend session, so a follow-up message picks up where the bot left off.
  - A task by a writing bot in a git repo gets its own worktree, on the branch `crewbots/<task id>`.
  - Every event is stored, so you can read the whole transcript later.
- **Delegation.** A coordinator (`can_delegate: true`, the chief in the starter crew) hands out work through crewbots' own MCP tools:
  - `delegate`, `tasks` and `message` are for coordinators only. Every bot gets `handoff` and `escalate`.
  - Delegated tasks run in parallel. The chief ends its turn instead of waiting, and when none of its tasks are still running, their handoffs and results arrive as its next message.
  - An escalation reaches it at once.
  - The harness enforces the rules:
    - only coordinators delegate, and never to another coordinator
    - a coordinator can only touch tasks it delegated
    - there is a cap on how many tasks one task can delegate
  - Each run's MCP server holds a token that acts only as that task, and only while the run lasts.
- **Review and merge.**
  - A review is a task delegated with `review_of`, so the reviewer works in the reviewed task's worktree. It ends with the `review` tool: a pass or fail verdict, and findings with locations and failure scenarios.
  - crewbots counts the rounds. A round is one reviewed version of the work, so a pass on an older version never approves newer changes. After three failed rounds it refuses both a fourth round and another revision, and the chief reports to you instead.
  - Work that passes every review of its current version can be proposed with `propose_merge`, which marks it `[ready to merge]`. Only you merge, with `crewbots merge <task>`. It commits the worktree, merges its branch into your checkout's current branch with a merge commit naming the reviewers, then removes the worktree.
  - It refuses to merge into a checkout with uncommitted changes, and aborts on a conflict, so nothing changes unless the merge is clean.
- **Skills** are [Agent Skills](https://agentskills.io) folders (a `SKILL.md` each). A role card lists the ones its bot gets, and the bot sees each skill's name, description and path, then reads the `SKILL.md` when a task matches.
  - The starter crew ships `interrogate`, `arena` and `swarm`: crew versions of [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s multi-agent skills. They use real model diversity, a Claude reviewer and a Codex critic.
  - Whole skill packs can be dropped in with `crewbots skills add`, and are namespaced (`pstack:tdd`).
- **The daemon** runs the tasks and serves an HTTP and WebSocket API on localhost. The desktop app and the `crewbots` command both use that API.
  - Every request needs the daemon's token, which is written to `daemon.json` and readable only by you.
  - The daemon also checks the Host header, so no web page you have open can drive your bots.
- **Backends** translate each CLI's JSON output into one shared event stream.
  - Bots get crewbots' MCP servers and their own, never your personal ones. On Claude that's `--strict-mcp-config`. On Codex, which has no such switch, every server, plugin and app from your config is turned off by name.
  - Each active task keeps its CLI running between turns, so follow-ups skip start-up. Idle sessions close after 10 minutes, and `serve --no-sessions` turns this off.
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

`init` creates the starter crew in `~/.crewbots/crew`: a chief of staff, an engineer (Codex), a reviewer (Claude) and a critic (Codex). Edit its role cards and team files to make it yours. In another terminal, give the chief a goal and follow the whole tree of work:

```bash
uv run crewbots task chief "Add a --verbose flag to the CLI, with tests. Review it with interrogate." --workspace ~/code/myproject
```

Or give one bot a task directly:

```bash
uv run crewbots task engineer "Add a --verbose flag to the CLI, with a test" --workspace ~/code/myproject
```

```bash
uv run crewbots say <task id> "Also document it in the README"
```

```bash
uv run crewbots diff <task id>
```

```bash
uv run crewbots review <task id>
```

```bash
uv run crewbots merge <task id>
```

`crewbots tasks` lists everything as a tree, `watch` follows a task and everything it delegates, and `cancel` stops one. Tasks still running when the daemon stops are marked interrupted; send them a message to carry on. `crewbots run` skips the crew and the daemon entirely and sends one prompt through one backend.

## The desktop app

The app is a [Tauri](https://tauri.app) shell (Rust) around a React UI. You need Rust (`rustup`) and Node.

```bash
cd app && npm install
```

```bash
npm run dev
```

`npm run build` makes the `.app`. To work on the UI in a plain browser, run `npm run vite` and open http://localhost:5173. Outside Tauri, the UI runs on a built-in demo crew (`src/preview/`) that covers every state the app shows: working, waiting on you, ready to merge, failed and merged.

The app is an Activity view of your crew:
- **Sidebar:** the task tree, filtered by all, needs you or running.
- **Each task:**
  - its live transcript, with tool calls, handoffs, verdicts and escalations
  - a review tab with the round and every reviewer's findings
  - a changes tab with the diff
  - a reply box and a merge button
- **New task:** pick a bot, a goal and a workspace.

The app is one conversation per goal:
- **Sidebar:** your goals, what needs you (questions, merges to approve, failures), and the crew, with who is working.
- **Thread:**
  - shows what was said and decided
  - folds tool use into one line ("Read 3 files, Ran 2 commands")
  - shows delegated work as cards with their review verdicts and a Merge button
- **Side panel:** opening a card shows that bot's own thread, its changes and its review.
- **Composer:** the one place you type, including a new goal with who it's for and the folder to work in.

If a daemon is already running the app attaches to it; otherwise it starts one, and stops it on quit.
- **The token stays out of the window.** The Rust side (`src-tauri/src/crew.rs`) holds the daemon's token, makes every call and relays `/v1/live` as events. The window gets nothing but IPC: its CSP allows no network.
- **Bot output is safe to show.** It's rendered as React elements, never as HTML.
- **`PATH` is fixed up.** An app launched from Finder gets a bare `PATH`, so the shell adds the usual tool folders before starting the daemon. Otherwise it couldn't find `uv`, `claude` or `codex`.
- **Pointing it elsewhere.** `CREWBOTS_HOME`, `CREWBOTS_PORT` and `CREWBOTS_PROJECT` choose another crew, port or checkout.
- **Self-test.** `CREWBOTS_SELFTEST=1` runs a check without anyone watching: connect, load, refuse `/bot/` paths, render the goals and a thread, open a task in the side panel, see live events. The app prints a report and quits.

## Skills from pstack

pstack's skills also work inside a single bot as they are. Its Claude Code port keeps relative links to its docs, so add the whole folder:

```bash
git clone --depth 1 https://github.com/cursor/plugins /tmp/cursor-plugins
```

```bash
uv run crewbots skills add /tmp/cursor-plugins/harness/pstack/claude-code --name pstack
```

Then list skills on a role card, for example `skills: [pstack:tdd, pstack:architect]` for the engineer. `crewbots skills` shows what's installed and which bots use each skill.

## Tests

```bash
uv run pytest -q
```

No test calls a model. Backend tests replay real CLI output (`tests/fixtures/`) through a fake CLI. Runner, delegation and API tests use an in-process fake backend whose scripted bots call the same methods the MCP tools reach.

## Layout

```
src/crewbots/
  events.py          the shared event stream
  backends/          claude_cli, codex_cli, and the subprocess runner they share
  crew.py            role cards and team files → bots and their instructions
  store.py           tasks and events, in SQLite
  runner.py          runs tasks: sessions, worktrees, queued follow-ups, delegation and reporting back
  api.py             the daemon: HTTP + WebSocket, user and run tokens, host checks
  mcp_server.py      the crewbots tools each bot gets (handoff, escalate, review; delegate, tasks, message, propose_merge)
  worktree.py        one git worktree per task
  cli.py             the `crewbots` command
  templates/crew/    the starter crew: bots, team files, skills
tests/               one file per module, plus the fakes and fixtures
app/                 the desktop app: src-tauri/ is the Rust shell, src/ the React UI, src/preview/ the demo crew
docs/                the design, and example role cards
```
