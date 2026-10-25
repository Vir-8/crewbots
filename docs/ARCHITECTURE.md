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
2. **The CLIs' own sandboxes.**
   - Read-only bots get read tools only.
   - Writing bots get Codex's `workspace-write` sandbox (writes confined to the worktree, no network by default) or Claude's accept-edits mode.
   - Next: turn on Claude Code's sandboxed shell for writing bots, through `--settings`, so Claude's shell commands get the same confinement as Codex's.
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
3. **UI exploration** (prototypes built). Three layouts run on real crew data behind a switcher: Conversation, Board and Inbox. Next, you use them, we pick or blend one, and build it properly.
4. **Long-lived sessions.** Codex app-server and Claude streaming input, with idle timeouts and the per-turn fallback, measured against the 2.9s baseline.
5. **Login service and menu bar.** launchd install and uninstall from the app, the app attaching rather than owning, the power assertion, notifications.
6. **Memory.** Bot memory files with `remember`/`forget`, FTS `recall`, and the rollover for long conversations.
7. **Routines, triggers, budgets.** The scheduler, GitHub triggers, per-bot budgets, and the standup and retro routines.
8. **Claude's sandboxed shell** for writing bots. Containers once there's a reason.

## Still open

- **Multiple crews:** whether crews share bots or memory (default: no, a crew is self-contained).
- **The chat bridge:** which app, and how much the chief can do from it (default: read status, answer questions, approve nothing irreversible).
- **Evals for crew behavior:** a suite in the style of the refund harness's evals. Does the chief delegate instead of doing the work? Does a reviewer fail planted bugs? Scored in code against fake and real runs.
- **Telemetry:** OpenTelemetry traces per task and turn, local only, for finding slow or expensive steps.
