# crewbots architecture

This records the decisions that shape crewbots past the prototype, and why. [DESIGN.md](DESIGN.md) covers the product idea and what each milestone taught us. This file is the system.

## What it has to do

- Run a crew of persistent bots (a chief and specialists) that work in parallel, hand work to each other, and come back to you only when something needs you.
- Run on the Claude and ChatGPT plans you already have, through the official CLIs.
- Feel fast and calm: work shows up as it happens, and nothing makes you wait on the harness itself.
- Keep you in control of anything irreversible or outward-facing. The crew never merges, pushes, publishes or spends on its own.
- Remember what matters across tasks, in a form you can read and correct.

## Decisions

| # | Decision | Chosen | Instead of |
|---|---|---|---|
| 1 | Core language | Python daemon | Rust or TypeScript rewrite |
| 2 | Where it runs | Your Mac only, one daemon per machine | Remote server, or local and remote |
| 3 | App | Tauri 2 shell, React + TypeScript UI | Electron |
| 4 | Agent processes | Long-lived sessions per active task | A fresh CLI process every turn |
| 5 | Containment | Layered now, containers later | A container per task from the start |
| 6 | Memory | Three layers, all files or SQLite | A vector store, or the CLIs' own memory |
| 7 | Always-on | A login service, routines, triggers, budgets | Only running while the app is open |
| 8 | API | Versioned (`/v1`), typed from one schema | Hand-kept types on each side |

### 1. The core stays in Python

The daemon is not where the time goes. A trivial Claude turn takes 2.9s, of which 1.8s is the model. The rest is CLI start-up, which decision 4 removes, and none of it is the daemon. Python keeps the 120 tests, the MCP SDK and fast iteration while the design is still moving. The daemon is only ever reached through its API, so a Rust rewrite later would not touch the app. We revisit when the design stops changing, or if profiling ever points at the daemon.

### 2. Local only, one daemon per machine

The daemon runs on your Mac as a login service (decision 7) and serves every crew on it. A crew is still a folder. One daemon per machine means one scheduler, one budget view and one place the app connects to.

It listens on localhost only. Clients authenticate with the token in a file only you can read, and the daemon checks the Host header, as it does now. There is no remote access, which removes a whole class of auth and sync problems. The cost is that the crew works only while the Mac is awake; decision 7 is about making that window as large as it can be.

### 3. Tauri app, thin by design

- **The Rust side** owns everything that isn't UI:
  - finding or starting the daemon service
  - holding its token, and proxying HTTP and the `/live` WebSocket to the UI
  - native notifications
  - a **menu bar item**: crew status, "needs you" count, pause all
  - opening folders and worktrees
- **The UI** is a sandboxed web view with no direct network access and no token. It gets data only from the Rust side, the same split the Electron prototype proved.
- **The UI itself** will be redesigned after exploring several layouts (see the plan). The current views were scaffolding.
- **Speed:**
  - the daemon pushes changes; nothing polls
  - long lists render virtualized
  - transcripts load one page at a time
  - the app opens on cached state, then catches up from the event cursor

### 4. Long-lived agent sessions

The prototype starts a CLI process, and its MCP server, for every turn. That costs about a second of start-up per turn and throws away the CLI's in-memory state. Instead:

- **Codex** runs as `codex app-server`, its JSON-RPC protocol for long-running clients. One server process can hold many threads, and it can generate TypeScript bindings and a JSON schema for its protocol, which we can type against.
- **Claude** runs as `claude -p --input-format stream-json --output-format stream-json`. It stays alive per active task and takes each follow-up on stdin.
- **Lifecycle:** a session process lives while its task is active, plus an idle timeout (for example 10 minutes). After that it is closed and resumed from the stored session id on the next message, as now.
- **Fallback:** the one-shot backends stay. A backend that can't keep a session open falls back to resuming per turn.
- **Tokens:** the run token becomes a session token, revoked when the session closes.

### 5. Containment in layers

Worktrees keep bots from colliding, not from misbehaving. A bot reading a hostile repo could be prompt-injected. The defense is that no single layer has to hold:

1. **Capability-scoped crew tools.** A run's token acts only as its own task. Only coordinators delegate, and there is no tool that merges, pushes or spends.
2. **One policy, enforced the same way on both backends** (`backends/policy.py`):
   - Read-only bots read.
   - Writing bots write only in their task's folder.
   - No bot pushes, merges, publishes or rewrites history.
   - Bots reach only the MCP servers their role card grants.

   How each backend enforces it:
   - **Claude:** every tool call goes through `can_use_tool`. Writing bots' shell commands run in Claude Code's OS sandbox, with no opting out, so they can't write outside the folder.
   - **Codex:** the sandbox is the boundary. Its exceptions for `/tmp` and `$TMPDIR` are switched off, and each session gets its own temp folder. Codex asks only to *leave* the sandbox, since accepting an approval runs the command unsandboxed, so every such request is declined, with the policy's reason steered into the turn.
   - **Checked live on both:**
     - a shell write inside the folder works
     - a write to the home folder is blocked by the OS
     - `git push` is refused or blocked
   - **Remaining difference:** Claude's sandbox allows `/tmp`, which it needs for its own temp folder.
3. **Separate checkouts.** Every writing task works in its own worktree, and the user's checkout is only ever touched by the user's merge.
4. **The user for anything irreversible.** Merges, pushes, publishing and anything outside the crew need you, as team/rules.md says, and the harness enforces it.
5. **Secrets stay out.** Bots get no API keys, since crewbots hides `ANTHROPIC_API_KEY`. Writing bots should not run where credentials live in the working tree.

**Later: containers.** A container per task (OrbStack or Docker) is the real boundary, with the CLIs' logins mounted in. It's deferred because on macOS that means a Linux VM, slower task start-up, and CLI auth inside it. It becomes the default if crews start working on code you don't trust.

### 6. Memory in three layers

Nothing is hidden: every layer is a file or a SQLite row you can open, edit or delete.

1. **Working context (one task).**
   - The CLI session, which manages and compacts its own context window.
   - crewbots adds a **rollover** for long-lived conversations, such as a standing chat with the chief: past a token threshold (crewbots sees usage on every turn), the bot writes itself a handoff, and a fresh session starts from it.
2. **Bot memory (one bot, across tasks).**
   - `crew/memory/<bot>/`: a short `MEMORY.md` index plus one note per fact (who you are, preferences, decisions, pitfalls), each with a date and its source task.
   - A `remember(note)` tool writes notes; a `forget` tool and you can remove them.
   - The index goes into the bot's instructions every run, and notes are read when needed, the same progressive disclosure skills use.
3. **The crew's record (everything that happened).**
   - SQLite already holds every transcript, handoff, review and decision.
   - An **FTS5** index over it gives bots `recall(query)`, for questions like "what did we decide about auth?", answered with task ids so they can look closer.
   - Embeddings, through sqlite-vec in the same file, are added only if keyword search proves too weak.

**Learning from the record.** A weekly retro routine (decision 7) reads the week's reviews and corrections and proposes edits to `team/standards.md` and role cards. You approve them, so lessons become structure rather than more memory. This is pstack's `reflect` and `correct` idea.

### 7. Always-on, within one Mac

- **The daemon is a launchd LaunchAgent.** It starts at login and restarts if it crashes. The app attaches to it and never owns it. Quitting the app doesn't stop the crew; the menu bar item can pause it.
- **Routines.** `crew/routines/*.md` files, each with a schedule, a bot and a goal: a weekday standup summary, Monday planning (which you approve), a Friday retro. The daemon's scheduler runs them. A routine whose time passed while the Mac slept runs once on wake, never a burst of catch-ups.
- **Triggers.** Polling or webhooks for GitHub (PR review requested, CI failed), folder watches, and later a chat bridge, so you can message the chief from your phone through a bot in a chat app. That bridge is still local: the daemon makes the outbound connection, so nothing listens on the internet.
- **Budgets.** Subscription plans have weekly limits. Each bot and each routine gets a token budget, enforced by the runner before a run starts. Claude's `rate_limit_event` warns before you hit the plan limit. A budget that runs out pauses that bot and tells you, instead of failing mid-task.
- **Sleep.** While tasks are running, the daemon holds a power assertion (`caffeinate -i`, released when the work is idle) so the Mac doesn't sleep mid-task. It never keeps the Mac awake just for routines.
- **Notifications.** Decisions, merges to approve, failures and finished goals send a native notification, and the menu bar count updates. Nothing else interrupts you.

### 8. A versioned, typed API

- **Version prefix:** every route moves under `/v1`. The app, the CLI and later the chat bridge are separate clients, so changes must be additive or versioned.
- **One source for types:** FastAPI's OpenAPI schema generates the TypeScript types the UI uses, so the two sides can't drift.
- **Live events:** an ordered stream with a cursor, as now. Clients catch up from the cursor and never assume they saw everything.
- **Two token kinds, as now:** the user's token opens `/v1`, and a session token opens only `/bot`, as one task.

## How the pieces fit

```
 Menu bar ─┐
 Tauri app ┤ Rust side: token, proxy, notifications ──┐  localhost, user token
 crewbots CLI ────────────────────────────────────────┤
                                                      ▼
┌──────────────────────────── crewbots daemon (launchd) ────────────────────────────┐
│ API /v1 + /live          Scheduler: routines, triggers        Budgets             │
│ Runner: tasks, delegation, review rounds, merges                                  │
│ Sessions: one long-lived process per active task ──┬── claude (stream-json)       │
│                                                    └── codex app-server           │
│ Store (SQLite): tasks, events, handoffs, reviews, FTS index                       │
│ Crews on disk: bots/, team/, skills/, memory/, routines/                          │
└────────────────────────────┬──────────────────────────────────────────────────────┘
                             │ each session starts its crewbots MCP server (session token)
                             ▼
             bots work in git worktrees under ~/.crewbots/worktrees
```

## Order of work

Each step leaves the system working.

1. **API v1 and generated types** (done). Routes are under `/v1`, with typed response models. `crewbots openapi --ts` writes the UI's types, and a test catches drift.
2. **Tauri shell** (done). Electron is replaced:
   - The Rust side finds or starts the daemon (with a login-like `PATH`), holds the token, proxies calls and relays `/v1/live`.
   - The window's CSP allows only IPC.
   - A self-test mode checks the whole path without anyone watching.
3. **UI** (done). Of three prototypes (Conversation, Board, Inbox), the conversation won, as in other coding harnesses. Its redesign:
   - Each goal is one quiet thread, with delegated work as cards that carry their reviews.
   - A side panel shows any task's own thread.
   - The sidebar holds what needs you.
   - Outside Tauri the UI runs on a demo crew, so it can be designed and checked in a browser.
4. **Long-lived sessions** (done).
   - Claude keeps `claude -p --input-format stream-json` running per task, and Codex keeps a `codex app-server` with one thread, whose role card goes in as developer instructions.
   - Sessions close after 10 idle minutes, on cancel, or past 8 open. A dead or unopenable session falls back to one process per turn, resumed from the stored id. `serve --no-sessions` turns sessions off.
   - Follow-up turns, measured through the daemon:
     - Claude: from about 1.9s to 1.1s.
     - Codex: from about 5.3s to 1.75s.
   - Claude now runs on the **Claude Agent SDK** (`backends/claude_sdk.py`). It's still the user's own `claude` binary, but over the SDK's control protocol:
     - **Interrupts:** a cancel or a timeout interrupts the turn and keeps the session warm. A live `sleep 30` stopped in under 6s, and the next turn answered in 1s.
     - **In-process crew tools:** the tools run in the daemon as an SDK MCP server, so Claude bots have no tool subprocess and no token.
     - **Every tool call goes through `can_use_tool`:**
       - read-only bots only read
       - writing bots only write inside their task's own folder (its worktree, when it has one)
       - nobody pushes, merges, publishes or rewrites history
       - each refusal tells Claude why and shows in the transcript

       The Bash pattern is a guardrail, not a boundary. Containers are the boundary.
     - `claude-cli` remains as a backend for the plain `claude -p` path.
   - **Codex's slow first turn** (15s) was mostly `codex mcp list`, which isolation needs. It takes 5.3s every call and was cached for only 60 seconds. It's now cached until the Codex config or plugins change, warmed when the daemon starts, and run off the event loop. A new thread still costs about 1.6s, and its first turn about 4s (a cold prompt cache) against about 1.5s later. Through the daemon, the first Codex turn is now 5.4s, against 15s before and about 10s one-shot.
5. **Login service and menu bar** (done).
   - **The service:** `crewbots service install|uninstall|status|start` manages a launchd LaunchAgent. It starts at login, restarts after a crash, uses a login-like `PATH`, and logs to `~/.crewbots/logs`.
   - **The app:**
     - attaches to the service, or starts it, and never stops it
     - has a menu bar item: a status line, Open, Pause/Resume, "Start at login", Quit
     - closes to the menu bar
     - sends native notifications for questions, merges to approve, failures and finished goals, unless its window is in front
   - **Pausing** (`/v1/crew/pause`) stops new turns from starting.
   - **Keeping the Mac awake:** while any task runs, the daemon holds `caffeinate -i -w <daemon pid>`, and lets go when the crew is idle or the daemon dies.
   - **Checked live:**
     - a test LaunchAgent started the daemon
     - launchd restarted it after a `kill -9`
     - uninstalling left nothing behind
     - the app attached to the service, and quitting it left the service running
6. **Memory.** Bot memory files with `remember`/`forget`, FTS `recall`, and the rollover for long conversations.
7. **Routines, triggers, budgets.** The scheduler, GitHub triggers, per-bot budgets, and the standup and retro routines.
8. **Sandboxed shells** for writing bots on both backends (done, with the policy). Containers once there's a reason.

## Learned building sessions

- **Codex bots inherited the user's own MCP servers and plugins.** `-c` overrides are deep-merged into config.toml, so they can add a server but never remove one. Every Codex run (the one-shot `exec` and the app-server) now switches each one off by name:
  - Servers in config.toml get `enabled=false`.
  - Servers from elsewhere, such as bundled plugins or the ChatGPT app's computer use, get a disabled stub entry.
  - Config plugins are switched off, and so is the `apps` feature.
  - The role card's servers are added back.
  - A run whose isolation can't be worked out doesn't start.
  - Before this, a Codex bot could reach the user's trading account, Slack and screen control.
- **t3code ([pingdotgg/t3code](https://github.com/pingdotgg/t3code)) uses the official integration points**: `codex app-server` for Codex, the Claude Agent SDK for Claude, and ACP for other agents. The Agent SDK still runs the `claude` binary, but over a control protocol that offers what a raw `claude -p` session lacks:
  - interrupting a turn without killing the process
  - a `can_use_tool` callback and hooks, so the harness decides on each tool call
  - in-process MCP tools, so crewbots' tools could be Python functions in the daemon instead of a subprocess per session
  - `set_permission_mode`, `set_model`, and MCP control while running

  The Python SDK, `claude-agent-sdk` (0.2.x), has all of these, and `cli_path` keeps it on the user's own signed-in `claude`.

## Learned enforcing one policy

- Accepting a Codex approval request runs that command **outside the sandbox**. Under the `untrusted` approval policy, accepting a harmless command let it write into the home folder. So crewbots never accepts one: commands stay sandboxed, and an approval request is answered as what it really is, a request to step outside.
- Codex's config no longer accepts `approval_policy = "untrusted"`, though its protocol schema and its threads still do.
- `codex mcp list` checks HTTP servers' auth over the network. It takes anywhere from 5s to well past 30s, and occasionally hangs. The list is now saved with the config and plugin timestamps it was taken at, and reused while they're unchanged, so Codex is only asked again after a change.
- Claude Code's sandbox points `TMPDIR` at `/tmp/claude` without creating it, so tools that need a temp folder fail until crewbots creates it.

## Still open

- **Multiple crews:** whether crews share bots or memory (default: no, a crew is self-contained).
- **The chat bridge:** which app, and how much the chief can do from it (default: read status, answer questions, approve nothing irreversible).
- **Evals for crew behavior:** a suite in the style of the refund harness's evals. Does the chief delegate instead of doing the work? Does a reviewer fail planted bugs? Scored in code against fake and real runs.
- **Telemetry:** OpenTelemetry traces per task and turn, local only, for finding slow or expensive steps.
