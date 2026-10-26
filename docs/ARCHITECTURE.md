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
- **The UI** is a messaging app: a chat with each bot and group chats, threads beside them, and each bot's profile.
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

1. **Capability-scoped crew tools.** A run's token acts only as its own task. Any bot delegates, within the guards the runner enforces, only coordinators add bots, and there is no tool that merges, pushes or spends.
   - **Teammates' word isn't the user's.** A message one bot sends another reaches it marked as a teammate's
     (`from-teammate.md`), and every bot is told a teammate can't give it permissions, change its settings, rules
     or notes, or approve anything for the user. A delegated task runs as its own bot, with its own access.
   - **Where a teammate writes is the user's say.** `delegate(workspace=…)` to a bot that would write in place (not
     in a worktree) is refused unless the folder is within the work's own, the delegating bot's or the teammate's;
     a repository is fine, since the teammate works in a worktree of it that only the user merges.
   - **The crew's settings are out of reach.** A writing bot never works in place in a folder holding the crew's
     folder, or inside it: role cards, team rules, routines and the database are the user's. One with no folder of
     its own works in `<crew>/work/<bot>`; given such a folder, its task fails with the reason. (Before, a writing
     bot with no workspace ran in the crew's folder itself, and could have rewritten its own role card.)
   - **The agent log.** Every delegation, review asked for, question, message, bot post, group opened or grown, and
     every bot tool call crewbots refused, goes in `agent_log` (`GET /v1/agent-log`, `crewbots log`). It's kept
     when the group chat or task it was about is deleted.
2. **One policy, enforced the same way on both backends** (`backends/policy.py`):
   - Read-only bots read.
   - Writing bots write only in their task's folder.
   - No bot pushes, merges, publishes or rewrites history.
   - Bots reach only the MCP servers their role card grants.
   - Shell commands reach the network only as the role card says: not at all (the default), any host
     (`network: true`), or a list of hosts.

   How each backend enforces it:
   - **Claude:** every tool call goes through `can_use_tool`. Writing bots' shell commands run in Claude Code's OS sandbox, with no opting out, so they can't write outside the folder.
   - **Codex:** the sandbox is the boundary. Its exceptions for `/tmp` and `$TMPDIR` are switched off, and each session gets its own temp folder. Codex asks only to *leave* the sandbox, since accepting an approval runs the command unsandboxed, so every such request is declined, with the policy's reason steered into the turn.
   - **Checked live on both:**
     - a shell write inside the folder works
     - a write to the home folder is blocked by the OS
     - `git push` is refused or blocked
   - **Network:** Claude's sandbox asks for each new host a command reaches, as a `SandboxNetworkAccess` tool call, and
     the policy answers from the role card, so a list of hosts works. Codex's sandbox has the network on or off for
     the whole run (`sandbox_workspace_write.network_access`), so a Codex card with a list is refused when the crew
     loads, rather than quietly given every host. Checked live on both: off, on, and Claude keeping to a list.
   - **No logins in the shell:** a writing bot's commands run as the user, so with the network on they could sign in
     as the user: on Codex, `gh api user` answered `Vir-8` and `git ls-remote` over SSH reached a private repo. Every
     writing bot now gets an environment without them (`backends/credentials.py`): an empty `GH_CONFIG_DIR`, a git
     config with only the user's name and email, the credential helper list cleared (Apple's git has its own
     `osxkeychain` default), an SSH command that offers no key, and no agent. Checked live: `gh` isn't logged in,
     SSH and HTTPS git are refused, and `pip install` still works. It isn't a wall: a command could still read
     `~/.ssh` or ask the keychain directly, since both sandboxes allow reads.
   - **Remaining difference:** Claude's sandbox allows `/tmp`, which it needs for its own temp folder.
3. **Separate checkouts.** Every writing task works in its own worktree, and the user's checkout is only ever touched by the user's merge.
4. **The user for anything irreversible.** Merges, pushes, publishing and anything outside the crew need you, as team/rules.md says, and the harness enforces it.
5. **Secrets stay out.** Bots get no API keys, since crewbots hides `ANTHROPIC_API_KEY`. Writing bots should not run where credentials live in the working tree.

**Later: containers.** A container per task (OrbStack or Docker) is the real boundary, with the CLIs' logins mounted in. It's deferred because on macOS that means a Linux VM, slower task start-up, and CLI auth inside it. It becomes the default if crews start working on code you don't trust.

### 6. Memory in three layers

Nothing is hidden: every layer is a file or a SQLite row you can open, edit or delete.

1. **Working context (one task).**
   - The CLI session, which manages and compacts its own context window.
   - crewbots keeps long-lived conversations, such as a standing chat with the chief, in shape (`runner/sessions.py`). Once a turn ends with the conversation past 65% of its context window (from each backend's own count), while the cache is warm:
     1. a hidden **flush** turn: the bot keeps the task's state (`update_state`) and writes a few lines for today's log (`flush.md`);
     2. the CLI **compacts** the conversation in place: Claude with `/compact` and the crew's instructions (`compact.md`: ids exact, the user's decisions first, the latest turns word for word), Codex with `thread/compact/start`. If the CLI already compacted as the flush began, it isn't done twice;
     3. the next turn is told its instructions and the task's state again (a `<crew-updates>` block), since a compaction may have summarised them away. A compaction the CLI does on its own, mid-turn, is followed the same way.

     A compaction that fails twice, or keeps almost nothing, carries on in a fresh session that starts from the bot's core memory, notes, last two days of logs, the task's state and the conversation's last eight messages; so does a conversation over 30% full that sat idle more than an hour, once the prompt cache has dropped it, since reading it all back in costs far more. About 50 minutes into such idleness, while the cache is still warm, the bot is flushed ahead of time, so the fresh session loses nothing. Each flush, compaction and fresh start is a quiet line in the thread. A backend that can't compact on request rolls over instead: the bot writes itself a briefing and the next message starts a fresh session from it. Checked live on both CLIs: a conversation grown turn by turn past 65% was flushed and compacted, and kept its codename and pull request. Claude Code's own compaction keeps about 30K tokens in reserve (it compacted at about 70K of a 100K window, about 85% of the default 200K), so 65% leaves it room at the default window. The window is explicit, per bot (`context_window`, 200K by default): Claude Code takes it as `--autocompact`, which also makes `/context` count against it, and Codex compacts at 85% of it (`model_auto_compact_token_limit`) while crewbots measures its usage against the smaller of it and the model's window. Measured against a model's whole window, a 1M-token model rolled over only at about 700K.
2. **Bot memory (one bot, across tasks).**
   - `crew/memory/<bot>/`: a short `MEMORY.md` index plus one note per fact (who you are, preferences, decisions, pitfalls), each with a date and its source task.
   - A `remember(title, body, shared?)` tool writes notes (`shared` puts them in `crew/memory/crew/`, for every bot); a `forget` tool and you can remove them.
   - The index goes into the bot's instructions every run, and notes are read when needed, the same progressive disclosure skills use.
   - Tiers around the notes (`memory.py`): a short `CORE.md` (yours and the crew's, cut at 6,000 characters) pinned
     in every session's instructions; a daily log, `daily/YYYY-MM-DD.md`, which gets a line for every handoff and
     whose last two days a new session starts with; and each task's `state/<task id>.md`, written whole by the bot
     with `update_state` at milestones and given to any fresh session of that task. `recall` searches the logs and
     notes as well as the record. Live on Haiku: a new session asked what it finished today answered from the log.
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
3. **UI** (done, then redone). Of three prototypes (Conversation, Board, Inbox), the conversation won, one per goal.
   Used for real, one-off goals turned out to be the wrong unit: the crew is meant to be always on, like ChatGPT
   Dots or Grok Bot. So it became a messaging app (see "Chats, threads and group chats" below).
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
6. **Memory** (done).
   - **Notes:** `remember`, `forget` and `recall` are crew tools on both backends. Notes are Markdown files with frontmatter (title, dates, source task), `MEMORY.md` is regenerated from them, and a note edited by hand is what the bot sees next run.
   - **Recall:** an FTS5 table (bm25, porter stemming) over messages, replies, handoffs, reviews, questions and task titles, filled as events arrive and backfilled the first time an older crew opens. It matches all words first, then any, and leaves out the asking task.
   - **Rollover:** as in decision 6, at 70%. The briefing turn is hidden from the task's result, and is skipped when the bot writes nothing.
   - **Checked live:**
     - a Claude reviewer remembered a crew note
     - a Codex critic in a new task answered from that note, with no tool call
     - a Codex bot used `recall` to find which earlier task decided an edge case
     - the rollover is covered by tests only, since filling a real context window costs too much to run
7. **Routines, triggers, budgets** (done).
   - **Routines:** `crew/routines/*.md`, re-read every tick (30s), so adding or editing one needs no restart. A file
     that doesn't parse is listed with why, and the others still run.
   - **Missed runs:** each routine keeps when it was last checked. A tick runs it if a scheduled minute fell after that,
     so a week of sleep is one run (looking back at most 7 days). Paused, nothing is checked, so it runs on resume.
     A new routine starts from its next time. One whose last run is still going is skipped and marked so.
   - **GitHub:** one GraphQL query through `gh` every five minutes: open pull requests that ask the user directly for
     a review (`user-review-requested:@me`; team requests left out, since on a big project they're dozens), and the
     user's own with their checks. Each trigger needs a `repo`. It starts on what's new since it was added, once per
     head commit, at most three a poll. Diffs and failed logs are fetched only when the goal uses them.
   - **Budgets:** per bot over a rolling day or week, and per routine run over the run's whole task tree, in fresh
     tokens. Checked before a turn, not during one. A held task holds no run slot and doesn't keep the Mac awake.
     Release lasts until the daemon stops; a reloaded crew or a release wakes held tasks to look again.
   - **Plan limits:** Claude's `rate_limit_event` and Codex's `account/rateLimits/updated` become one `RateLimit`
     event. A change of status is recorded and notified; a reached limit holds every bot on that plan until it resets.
   - **The app:** routines in the sidebar with their next run and Run now, held tasks under Needs you with "Go ahead
     anyway", plan limits above the crew, and a notification when a task is held.
   - **Checked live**, on a scratch crew:
     - an every-minute routine ran on the minute; the next run, over the critic's budget, was held and said why
     - the run after that was skipped, since the held one was still going, and releasing the held one let it finish
     - the standup ran from the real activity, looking back to the previous scheduled time
     - Claude's five-hour and Codex's weekly limits (45% used) came through the CLIs
     - the GitHub query ran through `gh`; with no open pull requests, nothing was started. Firing on a real pull
       request is covered by tests only
8. **Sandboxed shells** for writing bots on both backends (done, with the policy). Containers once there's a reason.

## Chats, threads and group chats

- **A chat** is a bot's one ongoing conversation with the user: a task of kind `chat`, made the first time it's
  needed, that never ends. Its length is handled by the memory work: notes, and a rollover briefing at 70%.
- **A thread** is work, opened with `start_thread` by any bot (or `delegate`, for a teammate) under the chat, seat
  or thread it's in. Results come back up as before. Budgets and "Go ahead anyway" apply per thread, the outermost
  thread above a task, never to a whole chat.
- **A room** is a group chat with a lead. Each bot in it has a **seat**, its own session for that room, fed the
  messages it hasn't seen as `[name]: text`; its final message is posted back as its reply, or nothing if it answers
  `NO_REPLY`. The user's message wakes whoever it mentions (`@everyone` is every member), or the lead, who triages:
  answers, hands it on with an @mention (one owner per piece of work), or stays out of it. A bot's reply wakes whoever
  it mentions, up to `MAX_ROOM_DEPTH` (4) replies from the user's message, or the room's own `max_depth`, so bots
  can't talk forever. A reply cut off there records `stopped` on the room (who was waiting); the app shows "The bots
  stopped after N replies" with Continue (`POST /rooms/{id}/continue`, waking them with a fresh count), and the
  user's next message clears it. A bot @mentioned by name owes a reply: a `NO_REPLY` from it gets one nudge
  (`mentioned.md`), and a second is accepted. The user manages a room with `PATCH /rooms/{id}` (title, lead, members,
  `max_depth`, muted, archived): a change to its title, lead or members closes its seats' idle sessions, so each
  bot's next turn starts from the room as it is (resuming its session with the new setting). Muted rooms aren't
  notified (the app's `CrewView` keeps them from `/rooms` and `room` messages). Archived ones wake no one; bots can't
  post there, and the user's next message brings them back. `DELETE /rooms/{id}` removes the room and its messages
  but keeps its seats, as the record of what they cost, and moves the work they handed out to each bot's chat;
  the app hears `room_gone`. The crew's own room is marked `crew` (migration 3 marks the oldest called "Crew"), so
  renaming it is safe; it can't be archived or deleted, and bots new to the crew join it. Checked live: asked to
  introduce themselves, the reviewer brought in the critic with a question, the critic answered, the reviewer
  replied, and it stopped. The user makes a room in the app; a bot makes one with `open_group`, holding at most
  `MAX_ROOM_BOTS` (6) bots, and any member can bring in another with `add_to_group`. Either way the app hears a
  `room` message and shows it at once.
- **Routines:** a scheduled one is a message in its bot's chat, from the routine; a trigger opens a thread under the
  bot's chat, with its per-run budget, and the chat hears how it went.
- **Unread** is kept in the daemon (`reads`), so the app and the menu bar agree.
- **Voice:** dictation uses `SFSpeechRecognizer` through a Swift sidecar (`app/src-tauri/dictation`), on device when
  the Mac supports it. The recognizer starts a new utterance after a pause and reports only its words, and a request
  ends after about a minute, so the helper keeps what it has heard and adds each utterance after it, starting a new
  request whenever one ends.
- **Codex for voice:** its app-server has an experimental realtime API (`thread/realtime/*`, behind
  `experimentalApi`), a spoken conversation rather than transcription. Tried with synthesized speech, it answers
  "realtime conversation requires API key auth": not available on a ChatGPT sign-in, so not for dictation here.
- **Models and reasoning:** a role card's `model` and `effort` apply to every turn the bot takes: its chat, its seats
  in group chats, and every thread it opens or is handed, since a thread runs as its owner. Claude takes the effort
  through the Agent SDK (`effort`: low to max), Codex per turn (`turn/start`'s `effort`, or `model_reasoning_effort`
  for `exec`). Blank means the CLI's own default. The app's dropdowns come from the CLIs (`models.py`): Codex's
  `model/list`, and the model list Claude Code reports when a session starts, cached for half an hour.
- **Code review** (`reviews.py`) is apart from the crew's always-on work. A review is one lead task of kind `review`
  and a child task per configured model (`reviewers:` in `reviews.yaml`), each named
  `reviewer:<backend>:<model>:<effort>` so it can be resumed from the name alone, read-only, with network (web
  search) and no roster, crew tools or memory.
  - `POST /reviews/{repo}/{n}/review` creates the lead (the first model, `QUEUED`, its first message just "Review
    <url>" and your note) and returns it at once; the checkout happens in the background, then `start_reviewers`
    starts the children in it. Each reviewer gets only the PR's link, your note and where the code is (`REVIEWER`),
    and asks for the rest with its tools: `pr_overview` (description, files, checks, linked issues, discussion) and
    `pr_diff` (a file at a time, or the whole diff in 40k-character parts of whole files).
  - When every child has finished, `_report_up` sends their findings, whole, to the lead as a message from
    crewbots (hidden in the app), and the lead runs with `LEAD_REVIEWER`: check each finding against the code, merge
    duplicates, cover what a failed reviewer missed, draft one line comment per problem, and reply with the review.
    Only the lead has `draft_comment`, so comments never overlap. Your messages to the lead before then wait.
  - The code: with a mapped local copy, the PR's head in a detached worktree of it (`~/.crewbots/worktrees/reviews`);
    otherwise crewbots' own blobless clone (`git clone --filter=blob:none --no-checkout` into
    `~/.crewbots/reviews/repos`, then `fetch pull/N/head` and `worktree add --detach` under `checkouts/`, six kept
    per repo; about a minute for a big repo's first review, seconds after). Only if git can't fetch it, a snapshot
    of the changed files through GraphQL, with `read_file` and `list_files` to read the rest on GitHub.
  - GitHub's API is for the PR itself, not for browsing code: its code search allows ten queries a minute, so there
    is none. Reviews are static: the prompts forbid running tests, builds, linters or installs.
  - Tools are served in-process to Claude and over MCP (`CREWBOTS_TOOLS=review`, `CREWBOTS_REVIEW=github,drafts`)
    to Codex. GitHub's inbox and pull requests are cached for a minute or two.
- **Prompt caching** is the CLIs' and the APIs', matched from the start of the prompt. A bot's instructions put what
  stays the same first (who it is, its role card, the roster, the crew tools, skills, team files) and what changes
  last (its notes, the task's setting, a briefing or review), so every session of a bot shares the cached start, and
  a new note only changes the end. Analytics shows how much input came from the cache, per model and bot.
- **Changes reach a running session in the turn.** A session keeps the instructions it started with (both CLIs do),
  so the role card, roster, skills, team files and notes index (`Crew.living_parts`) are fingerprinted when a session
  starts (the `primed` table), and a later turn carries whatever changed since in a `<crew-updates>` block before the
  message (`crew-updates.md`). The fingerprint moves on only once that turn goes through, so a failed one is told
  again. The cached start stays as it was: live, a Haiku session told of a team file added after it began read 12.9K
  of 13.5K tokens from the cache.
- **The network setting** covers more than shell commands: Claude Code runs WebFetch and WebSearch itself, outside
  the shell's sandbox, so the policy checks them too (a fetch against the host list; a search only with any host),
  and Codex's own web search is switched off (`web_search="disabled"`) for a bot without network.
- **MCP servers** (`mcp_catalog.py`): the crew's are in `mcp.json`, in Claude's `mcpServers` shape, and role cards
  name the ones their bot gets. Codex takes them as `-c mcp_servers.<name>.*` (a `url` and `http_headers` for HTTP;
  it can't do SSE), Claude as the Agent SDK's `mcp_servers`. The app can copy a server from Claude Code's,
  Claude's desktop app's or Codex's config, add one from the official registry (`registry.modelcontextprotocol.io`:
  a package becomes `npx`, `uvx` or `docker run`; a remote becomes a URL), and check one by starting it and listing
  its tools with the MCP client. The file can hold keys, so it's the user's alone (0600), and the API reports only
  which env and header names a server has, never their values.
- **Skills** come from the crew's `skills/` and the user's global folders; a global one that can't be read, or one a
  CLI keeps for itself (Codex's `.system`), is left out, and the crew's own win a clash of names.
- **Delegation** is any bot's: it hands a piece of work to a teammate, which runs it as itself, with its own access. The runner stops it from running away (`runner/delegation.py`): nothing to yourself, to a coordinator or back up the chain, three delegations deep at most, four unfinished delegated tasks per bot, and nothing past a budget.
- **`add_bot`**, for coordinators only: it writes the role card through the same code as the app, checks the crew
  still loads (or takes the card back), and reloads the crew. The new bot has what the coordinator gives it, except
  full access (the user's to give) and network when the coordinator has none. There is no tool to remove or edit a
  bot.
- **Resizing panes** (`Resizer.tsx`): each pane's width is a CSS variable on the grid it's part of, set straight on
  the element while dragging (so a re-render mid-drag can't undo it) and kept in localStorage when the drag ends. The
  handle limits a drag to what the middle column can give up, the grid's `minmax` tracks let side panes give way in a
  narrow window, and the CSS defaults stand in until a pane is dragged.
- **Review comments** (`review_drafts` in the store): a reviewer's `draft_comment` and the user's own drafts are
  checked against the PR's diff the way GitHub checks them (`commentable`: RIGHT is the head's added and unchanged
  lines, LEFT the base's removed and unchanged ones) and kept with the head they were written on. Submitting is a
  single `gh api POST /repos/{repo}/pulls/{n}/reviews` with the event, the summary and the drafts, sent once (a
  write isn't retried: one that timed out may have happened), under the `/v1/reviews` routes, which only the user's
  token opens; a bot's token reaches `/v1/bot` alone. A review's lead drafts; its reviewers only report findings.
- **Changed images** in a PR come from GitHub's contents API at the head and the base commit (the blobs API past a
  megabyte), kept like other GitHub answers.
- **Attachments** (`attachments.py`) are files in the crew's `attachments/<id>/`, which Claude bots may read (an
  `add_dirs` entry) and Codex bots read anyway. A message records them (a task event's `attachments`, a room
  message's column) and the text a bot gets lists their names, types and paths. The app uploads bytes (picked,
  pasted or dropped: the window's own file-drop handling is off so the page gets the files) and shows images by
  asking for them back.
- **`/status`** asks the runner for a task's session and context: measured live when its session is open and idle,
  else the last measurement, kept after every turn.
- **When a turn fails:** the CLIs retry a failed API call themselves first (Claude Code with backoff, Codex per
  request and per stream, `request_max_retries` and `stream_max_retries`). When they give up, the backend says whether the failure is
  passing, from what the CLI reports rather than its wording: Codex's `codexErrorInfo` (overloaded, server error,
  connection or stream failures; usage or rate limits), the Agent SDK's error kind and `api_error_status` (5xx and
  529; 429), and a CLI that died or broke its protocol. The runner then tries the turn again after 30s, 2 min and
  5 min, resuming the session with a note to carry on and check what's already done, and leaves it failed after the
  fourth try. A plan limit with a known reset waits in the existing hold until it resets instead. A turn with no
  event at all for 20 minutes is stopped and tried the same way (Claude's commands can't run silent past 10). Auth,
  context window, bad request and policy failures aren't retried: they'd fail the same way again.
- **GitHub's answers are kept** (`Reviews._kept`): an inbox list, a pull request, its diff, a file or an issue comes
  back at once from the last answer, and one older than a minute or two is fetched again behind it, one fetch per
  item at a time. Only a refresh you ask for, or nothing kept yet, waits for GitHub, and a failed fetch keeps the
  last good answer, so a 504 never empties the list. The daemon asks for your lists when it starts; your teams' list
  only when you open it. The app keeps pull requests and diffs too, so going back to one shows it at once.
- **GitHub:** every `gh` call is tried again twice, after 2s and 5s, when GitHub or the network failed for a passing
  reason (5xx, GraphQL's "something went wrong", which is its query timeout, timeouts and dropped connections). The
  code review inbox asks for each of its four lists on its own, in parallel: in one query they took GitHub 7 to 8s
  for a busy account, close to its GraphQL time limit (the 504s), and apart the slowest takes about 4s.
- **Analytics** (`usage.py`, `plans.py`): every turn's usage event carries its plan, model and effort, so the page
  can split the crew's fresh tokens by day, plan, model, bot and effort (older events fall back to the bot's current
  model). The store sums them in SQL by local day, so a year of turns comes back as a few rows. Where the plans
  stand is asked of the CLIs, without a model call: Claude Code's `/usage` command run with `-p` prints the
  session's and weeks' percentages and resets as text, and Codex's app-server answers `account/rateLimits/read`.
  Nothing about a particular plan is built in: limits are named in each CLI's terms ("Session", "Week · Fable",
  "5 hours"), Codex's every metered bucket is shown, and what else they report (Codex credits and free resets,
  Claude's reason when there are no limits, such as an API key) shows as notes.
  Starting the CLIs takes seconds (Claude about 2.5s even without MCP servers, Chrome or a saved session; 1 to 2s for
  Codex), so the daemon asks when it starts and keeps the answers: an answer over two minutes old is served at once
  while a new one is asked for behind it, one ask per CLI at a time, and a failed ask keeps the last good answer.
- **Editing the crew** (`cards.py`): role cards and routines are rewritten from the app with the frontmatter it
  doesn't edit kept, after the crew's own parser accepts the new text, and atomically. YAML comments don't survive.
  A bot's handle stays fixed (tasks and @mentions use it); its display name, which @mentions also match, can change.

## Performance budgets

Speed is a feature, so it's measured, with budgets that fail a check when something gets slow:

- **The daemon** (`uv run scripts/perf.py`): a crew of 10 bots, 2,000 threads, 247,000 events and 20,000 group chat
  messages, built in a temp folder in about 6s and served over real HTTP. Medians on an M-series Mac, against budgets:
  `/tasks?limit=1000` 32ms (120), `/chats` 23ms (60), a group chat's messages 5ms (60), a 2,000-event chat's events
  16ms (80), `/events/recent` 5ms (40), two-word search 2ms (60), a search for a word in everything 67ms (250), a
  profile 4ms (60), and a message reaching a `/v1/live` client 7ms (30).
- **The window** (`npm test`): a 2,000-event chat renders in about 33ms in Node (budget 150), before layout. In the
  design preview (`?big=2000`, React's slower development build) opening it takes about 110 to 125ms with about 6,600
  elements; windowing long lists comes next.
- **The app** (`CREWBOTS_SELFTEST=1`): the self-test reports how long the window took to draw its first chat, to open
  another, and for a word from the daemon to reach it.

## Learned checking the CLIs' sessions and compaction

Checked on Claude Code 2.1.310 and codex-cli 0.164.0 (26 October 2026), before building memory on them:

- **A session's system prompt is fixed at its start.** Claude records it on the first request and reuses it on every
  resume (`--system-prompt-snapshot`, on by default), so a note written later never reaches a resumed session.
  Compacting refreshes it. Turning the snapshot off applies new text, but each change rewrites the cache for the
  prompt and the whole history after it. Codex's `thread/resume` keeps the first `developerInstructions` too. So
  what changes goes in the turn, not the system prompt.
- **Claude compacts on request.** `/compact <instructions>` sent as an ordinary message gives a `compact_boundary`
  event with the token counts (36.5K to 8.3K in about 3s). Automatic compaction (`--autocompact`, or
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW`) runs at the start of a turn, keeps the last few messages word for word, and
  triggered later than its window suggests (not at 100.5K for a 100K window, but at 158K).
- **Claude's hooks work from `--settings`.** PreCompact runs before, PostCompact gets the summary's text, and a
  SessionStart hook with the matcher `compact` can add context the model reads after compacting.
- **Forks differ.** A Claude fork (`--fork-session`) keeps the first system prompt and reads the warm cache. A Codex
  fork (`thread/fork`) keeps everything but starts with a cold cache.
- **Codex compacts remotely.** With a ChatGPT sign-in, `thread/compact/start` and `model_auto_compact_token_limit`
  replace the history with an encrypted item from the server, with no summary to read, and keep the user's own
  messages word for word, so a limit below their size compacts on every turn. `thread/inject_items` adds a
  developer message without breaking the cache.

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

## Learned polishing the app

- The Intel tools here came from an Intel Homebrew in `/usr/local`, ahead of `/opt/homebrew` in PATH. Installing an
  Apple silicon node isn't enough: `npm` is a script that runs whichever `node` comes first, so it kept installing
  Intel binaries (codex picks its native binary by node's architecture) until the Apple silicon node came first.
- Vite's dev server hangs at start on node 26; node 22 runs it. `vite build` works on both.
- Claude Code reports its models only by family ("Sonnet"), with the version in the description; the app labels them
  by version, and lists pinned versions (`claude-opus-4-8`) apart from the names that follow the newest.

- macOS warned that crewbots "includes a component that will not work with a future release": the dictation helper
  was Intel. The `node` here is an x86_64 build, so `npx tauri build` ran under Rosetta and `swiftc` defaulted to
  x86_64, while Rust still built for Apple silicon. The build now names the helper's target outright.
- The first avatar colors failed the palette checks on the dark surface (too light, and pink and coral too close). The
  faces now use the validated categorical palette, which the usage page's plan colors also come from.
- The analytics page waited for every request together, so the plans' few seconds of CLI start-up held up numbers
  the store had in milliseconds. Now each part shows when it has its numbers, and the plans come from the daemon's
  kept answers.
- macOS window buttons in an overlay title bar sit 2px above `trafficLightPosition`'s `y` at their centre (measured
  with AppKit: a 14px button whose top is `y - 9`), so `y: 30` centres them on the 56px headers' line.
- Claude Code has no machine-readable limits: `/usage` prints lines like "Current week (all models): 12% used ·
  resets Oct 28 at 11:30pm (Asia/Calcutta)", so a change to that wording will show as no limits until the parser
  follows it. Codex reports the same through its app-server as data.

## Learned enforcing one policy

- Accepting a Codex approval request runs that command **outside the sandbox**. Under the `untrusted` approval policy, accepting a harmless command let it write into the home folder. So crewbots never accepts one: commands stay sandboxed, and an approval request is answered as what it really is, a request to step outside.
- Codex's config no longer accepts `approval_policy = "untrusted"`, though its protocol schema and its threads still do.
- `codex mcp list` checks HTTP servers' auth over the network. It takes anywhere from 5s to well past 30s, and occasionally hangs. The list is now saved with the config and plugin timestamps it was taken at, and reused while they're unchanged, so Codex is only asked again after a change.
- Claude Code's sandbox asks for network access one host at a time through `can_use_tool`, as a tool named
  `SandboxNetworkAccess`. The policy allowed tools it didn't know, so until the `network` setting, Claude's writing
  bots could reach any host while Codex's could reach none.
- On Codex, crewbots' rules only apply when Codex asks to leave its sandbox; a command that stays inside runs without
  asking. With no network, that sandbox was what kept a bot from pushing. With the network on, only the absence of
  credentials does.
- Codex can approve one host at a time only through its experimental `network_proxy` feature, which is off by
  default. Worth revisiting once it's stable, for host lists on Codex too.
- Claude Code's sandbox points `TMPDIR` at `/tmp/claude` without creating it, so tools that need a temp folder fail until crewbots creates it.

## Still open

- **Multiple crews:** whether crews share bots or memory (default: no, a crew is self-contained).
- **The chat bridge:** which app, and how much the chief can do from it (default: read status, answer questions, approve nothing irreversible).
- **Evals for crew behavior:** a suite in the style of the refund harness's evals. Does the chief delegate instead of doing the work? Does a reviewer fail planted bugs? Scored in code against fake and real runs.
- **Telemetry:** OpenTelemetry traces per task and turn, local only, for finding slow or expensive steps.
