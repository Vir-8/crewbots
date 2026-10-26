# crewbots

A crew of persistent AI agents that you manage instead of babysit. You give a goal to a chief of staff, which splits it into tasks with one owner each and hands them to specialist bots: an engineer, a reviewer, or whatever you add, such as a video editor working in DaVinci Resolve. The bots run in parallel, each writing bot in its own git worktree, and you hear back when there is something to review or decide.

The agents run on the official **Claude Code** and **Codex** CLIs, so they use the Claude and ChatGPT plans you already have. Claude Code is driven through the **Claude Agent SDK**, and Codex through **`codex app-server`**, the two tools' own interfaces for programs. crewbots is the layer above them: bots, tasks, worktrees, delegation, rules and the app. The plan is in [docs/DESIGN.md](docs/DESIGN.md), and the architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
- **Memory.** Every bot can `remember` a fact for later tasks (its own, or `shared` with the crew), `forget` one, and `recall` what the crew said or decided before.
  - Notes are files in `crew/memory/<bot>/notes/`, listed in each bot's instructions, so you can read, edit or delete them.
  - `recall` searches the whole record with SQLite FTS5, and so does `GET /v1/search?q=`.
  - A conversation that fills 70% of its context continues in a fresh session, from a briefing the bot writes.
- **Routines** are work the crew starts on its own: Markdown files in `routines/`, each naming a bot, a goal, and when.
  - A `schedule` is cron in your time zone. A routine missed while the Mac slept runs once when it wakes, never once
    per miss, and one whose last run is still going is skipped that time.
  - `on: github.review_requested` or `on: github.ci_failed` starts it when a pull request in its `repo` asks you for a
    review, or one of yours fails its checks. The daemon asks GitHub every five minutes with your `gh` login, so
    nothing listens on the internet. A trigger only starts on what's new after it's added, and once per pushed version.
  - The goal can use what the run is about: `{{activity}}` (what the crew did since the last run), `{{pr.diff}}`,
    `{{pr.failures}}` (failed checks with the end of their logs), and more (`src/crewbots/routines.py`).
  - A new crew comes with a weekday standup and a Friday retro, which proposes edits to the crew's own files, plus two
    GitHub triggers that start switched off.
- **Budgets.** A role card can set `budget: {tokens: 2000000, per: week}`, and a routine a token budget for one run,
  counting everything it delegates. Tokens are the fresh ones: input the model hadn't cached, plus output.
  - Before each turn the runner checks them, and the plan's own limit, which both CLIs report. When one is spent, the
    task is **held**: its next turn waits, it tells you, and it goes on by itself once there's room, or at once when you
    release it. A turn already running is never cut short.
- **The daemon** runs the tasks and serves an HTTP and WebSocket API on localhost. The desktop app and the `crewbots` command both use that API.
  - Every request needs the daemon's token, which is written to `daemon.json` and readable only by you.
  - The daemon also checks the Host header, so no web page you have open can drive your bots.
- **Backends** translate each CLI's output into one shared event stream.
  - **One policy for both backends** (`backends/policy.py`):
    - read-only bots only read
    - writing bots only write in their task's folder
    - nobody pushes, merges or publishes
    - writing bots' shell commands have no network unless their role card says `network: true`, or lists the hosts
      (`network: [pypi.org, files.pythonhosted.org, "*.npmjs.org"]`). A list needs Claude: Codex's sandbox only
      switches the network on or off.
    - writing bots' shells never get your logins: `gh` sees an empty config, git has no credential helper and offers
      no SSH key, and there's no SSH agent. Your git name and email stay. This stops ordinary signing in, not a
      determined command, which could still read a key file; containers are that boundary.
  - **How it's enforced:** Claude asks on every tool call. Codex's sandbox is the boundary, and every request to leave it is declined. Writing bots' shell commands are OS-sandboxed on both.
  - **Claude's tools run inside the daemon.**
  - **Interrupts:** a cancelled or timed-out Claude turn is interrupted, not killed, so its session stays warm.
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

To keep the crew running whenever you're logged in, even with the app closed, install the daemon as a login service:

```bash
uv run crewbots service install
```

It starts at login and restarts if it ever crashes. `crewbots service status` and `crewbots service uninstall` check on it and remove it, and its log is `~/.crewbots/logs/daemon.log`. The app's menu bar item has the same switch ("Start at login"). While tasks are running, the daemon keeps the Mac from sleeping (`caffeinate`), and it lets go as soon as the crew is idle.

`crewbots routines` lists the routines with their next run, `routines run standup` runs one now, and `routines add`
copies in a starter one. `crewbots budgets` shows each bot's use against its budget and where the plans' limits
stand, and `release <task>` lets a held task go ahead. Edited role cards, team files, skills and routines count
within 30 seconds, with nothing to reload.

`crewbots tasks` lists everything as a tree, `watch` follows a task and everything it delegates, and `cancel` stops one. Tasks still running when the daemon stops are marked interrupted; send them a message to carry on. `crewbots run` skips the crew and the daemon entirely and sends one prompt through one backend.

## The desktop app

The app is a [Tauri](https://tauri.app) shell (Rust) around a React UI. You need Rust (`rustup`) and Node.

```bash
cd app && npm install
```

```bash
npm run dev
```

To install it, build the `.app` and copy it to Applications:

```bash
npx tauri build --bundles app
```

```bash
cp -R src-tauri/target/release/bundle/macos/crewbots.app /Applications/
```

The installed app runs the daemon from this checkout, the path it was built in, so keep the checkout where it is (or
set `CREWBOTS_PROJECT`). Besides goals and their conversations, the app has a **Crew** page: each bot with what it
may do (access, network, MCP servers, skills), its budget use, and a button to edit its role card; the plans' limits;
and the routines, with Run now, editing, and the starter routines to add. To work on the UI in a plain browser, run `npm run vite` and open http://localhost:5173. Outside Tauri, the UI runs on a built-in demo crew (`src/preview/`) that covers every state the app shows: working, waiting on you, ready to merge, failed and merged.

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

**The menu bar item:**
- what's happening ("2 working · 1 needs you")
- Open
- Pause the crew: no new turns start, and running ones finish
- Start at login
- Quit

Closing the window leaves crewbots in the menu bar. You get a native notification when a goal has a question, work is ready to merge, a goal fails, or a goal is done, unless you're already looking at the app.

**Finding the daemon:** if one is already running, the app attaches to it. If not, the app starts the login service when it's installed, or its own daemon otherwise, which it stops when it quits. The app never stops the login service.
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
  memory.py          bot and crew notes, as files
  routines.py        routine files, cron schedules and starter routines
  scheduler.py       runs routines when they're due and triggers when GitHub has something new
  github.py          what the GitHub triggers read, through `gh`
  store.py           tasks and events, in SQLite, with full-text search
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
