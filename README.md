# crewbots

A crew of always-on AI agents that you message like teammates. Each bot, a chief of staff, an engineer, a reviewer, or whatever you add such as a video editor working in DaVinci Resolve, has one ongoing chat with you. Real work goes into threads they open beside the chat, each writing bot in its own git worktree. Bots talk to each other in group chats, run routines on their own, and you hear back when there's something to review or decide.

The agents run on the official **Claude Code** and **Codex** CLIs, so they use the Claude and ChatGPT plans you already have. Claude Code is driven through the **Claude Agent SDK**, and Codex through **`codex app-server`**, the two tools' own interfaces for programs. crewbots is the layer above them: bots, tasks, worktrees, delegation, rules and the app. The plan is in [docs/DESIGN.md](docs/DESIGN.md), and the architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How it works

- **A crew is a folder.** `bots/` has one role card per bot, `team/` has the files every bot reads (mission, standards, protocol, rules), and `skills/` has the skills bots can use.
- **A role card** is Markdown with frontmatter:
  - The frontmatter is what the harness enforces: backend, model, access level, workspace, and the bot's own MCP servers.
  - The body is the card itself: its mission, what it owns, what it does *not* own, and what done means.
  - See [docs/examples/video-editor.md](docs/examples/video-editor.md).
- **Chats.** Each bot has one ongoing chat with you. It never resets: the bot's notes, and a briefing when it grows long, carry it from day to day. A scheduled routine posts into its bot's chat too, as a message from the routine.
- **Threads.** For real work, a bot opens a thread (`start_thread`) beside the chat it's in, works it in parallel, and the result comes back to the chat. A coordinator can also hand a thread to another bot (`delegate`). Threads are where worktrees, reviews and merges happen.
- **Group chats.** You and some bots, with one bot as the lead. A message that mentions no one goes to the lead, who answers it, hands it to the teammate best placed for it, or leaves it to whoever already has it. An @mention brings that bot in and always gets its reply, and `@everyone` brings them all in. Bots @mention each other the same way, up to four replies in a row (each group chat can set its own limit); then the chat says the bots stopped, and **Continue** lets them carry on. A group chat's panel renames it, changes who's in it and who leads, sets its reply limit, mutes it, and archives it (kept, folded away at the foot of the list, until you say something there) or deletes it (the work it handed out stays, reporting to each bot's own chat). The crew's own group chat can't be archived or deleted, and bots new to the crew join it. Every crew has a "Crew" group chat with all its bots, and a bot can speak there from anywhere with `post`. A bot can open a group chat of its own with `open_group` (up to six bots) and bring a teammate into one it's in with `add_to_group`; it shows in the app at once.
- **A task** is the unit underneath: a chat, a bot's seat in a group chat, or a thread.
  - Each task has exactly one owner bot and its own backend session, so a follow-up message picks up where the bot left off.
  - A task by a writing bot in a git repo gets its own worktree, on the branch `crewbots/<task id>`.
  - Every event is stored, so you can read the whole transcript later.
- **Delegation.** Any bot hands a piece of work to the teammate best suited to it through crewbots' own MCP tools:
  - `delegate`, `tasks` and `message` are every bot's, as are `handoff` and `escalate`. A coordinator
    (`can_delegate: true`, the chief in the starter crew) also adds bots (`add_bot`) and proposes work for merging.
  - Delegated tasks run in parallel, each as its own bot with its own access. The delegating bot ends its turn instead
    of waiting, and when none of its tasks are still running, their handoffs and results arrive as its next message.
  - `ask(bot, question)` asks a teammate a quick question instead: the teammate answers read-only, with no worktree,
    starts nothing from it, and the answer comes back the same way. The app shows it as a card, "Asked Critic: …
    Answer: …".
  - An escalation reaches it at once.
  - The harness enforces the rules:
    - never to yourself, nor to a coordinator, nor back to a bot already in the chain (a circle)
    - a chain at most three delegations deep (`crewbots serve --max-delegation-depth` changes it)
    - at most four unfinished delegated tasks per bot at once, and twelve from one task
    - no delegating past a budget or a reached plan limit, the delegating bot's or the teammate's
    - a bot can only touch tasks it delegated
  - Each run's MCP server holds a token that acts only as that task, and only while the run lasts.
- **Review and merge.**
  - A review is a task delegated with `review_of`, so the reviewer works in the reviewed task's worktree. It ends with the `review` tool: a pass or fail verdict, and findings with locations and failure scenarios.
  - crewbots counts the rounds. A round is one reviewed version of the work, so a pass on an older version never approves newer changes. After three failed rounds it refuses both a fourth round and another revision, and the chief reports to you instead.
  - Work that passes every review of its current version can be proposed with `propose_merge`, which marks it `[ready to merge]`. Only you merge, with `crewbots merge <task>`. It commits the worktree, merges its branch into your checkout's current branch with a merge commit naming the reviewers, then removes the worktree.
  - It refuses to merge into a checkout with uncommitted changes, and aborts on a conflict, so nothing changes unless the merge is clean.
- **Prompts are files.** Everything crewbots itself tells the models is markdown in `src/crewbots/prompts/`: how to
  work in the crew (`crew-tools.md`) and delegate (`delegation.md`), a chat's and a group chat's setting, the memory
  guide, the briefing, retry and resume turns, the code review prompts (`review/`), and each crew tool's description
  (`tools/<name>.md`). Put a file at the same path under the crew's `prompts/` folder to replace one for your crew
  (`~/.crewbots/crew/prompts/review/lead.md`); delete it to go back. `{name}` placeholders are filled in, and an edit
  counts from the next turn.
- **Skills** are [Agent Skills](https://agentskills.io) folders (a `SKILL.md` each). A role card lists the ones its bot gets, and the bot sees each skill's name, description and path, then reads the `SKILL.md` when a task matches.
  - The starter crew ships `interrogate`, `arena` and `swarm`: crew versions of [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s multi-agent skills. They use real model diversity, a Claude reviewer and a Codex critic.
  - Whole skill packs can be dropped in with `crewbots skills add`, and are namespaced (`pstack:tdd`).
- **Memory.** Every bot can `remember` a fact for later tasks (its own, or `shared` with the crew), `forget` one, and `recall` what the crew said or decided before.
  - Notes are files in `crew/memory/<bot>/notes/`, listed in each bot's instructions, so you can read, edit or delete them.
  - `recall` searches the whole record with SQLite FTS5, and so does `GET /v1/search?q=`.
  - A conversation that fills 70% of its context window continues in a fresh session, from a briefing the bot
    writes. The window is the role card's `context_window` (200K tokens unless it says otherwise, up to `1m`, or
    `auto` for the model's whole window): the CLI compacts within it, so a 1M-token model doesn't carry 700K
    tokens before rolling over. `/status` shows how full it is.
  - Bots never use the CLIs' own memory: Claude Code's auto memory and Codex's memories are off for every bot, so
    nothing a bot learns lands beside your own Claude or Codex memory.
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
    - a bot has no network unless its role card says `network: true`, or lists the hosts
      (`network: [pypi.org, files.pythonhosted.org, "*.npmjs.org"]`). That covers web search and fetching pages, for
      any bot, and a writing bot's shell commands. A list needs Claude, which checks each page's host: Codex only
      switches the network on or off, and a web search can lead anywhere, so a list allows pages but not search.
    - writing bots' shells never get your logins: `gh` sees an empty config, git has no credential helper and offers
      no SSH key, and there's no SSH agent. Your git name and email stay. This stops ordinary signing in, not a
      determined command, which could still read a key file; containers are that boundary.
  - **How it's enforced:** Claude asks on every tool call. Codex's sandbox is the boundary, and every request to leave it is declined. Writing bots' shell commands are OS-sandboxed on both.
  - **Claude's tools run inside the daemon.**
  - **Interrupts:** a cancelled or timed-out Claude turn is interrupted, not killed, so its session stays warm.
  - Bots get crewbots' MCP servers and the ones their role card names (`mcp: [blender, gimp]`), from the crew's own
    list in `mcp.json`, never your personal ones. On Claude that's `--strict-mcp-config`. On Codex, which has no such
    switch, every server, plugin and app from your config is turned off by name. A server is a program the CLI
    starts, or a URL (streamable HTTP, or SSE on Claude).
  - **Skills:** every bot sees the crew's skills and your global ones (`~/.claude/skills`, with those synced from
    claude.ai, `~/.agents/skills` and `~/.codex/skills`), by name and description, and reads a skill's `SKILL.md` when
    a task matches it, the way Claude Code does. A role card's `skills:` can name only some, or `none`.
  - **Adding bots:** the chief (any bot that coordinates) can add a bot when you ask it to (`add_bot`), with a role
    card it writes and whatever it chooses to give: either access short of full, delegation, any of the crew's MCP
    servers (all of them unless it names some), and network unless the chief has none itself. No tool removes or
    changes a bot: that's yours, in the app.
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

It starts at login and restarts if it ever crashes. `crewbots service status` and `crewbots service uninstall` check on it and remove it, and its log is `~/.crewbots/logs/daemon.log`. `crewbots restart --when-idle` restarts it once no turn is running (no new one starts meanwhile, and after 20 minutes the turns still running are cut off and carry on after), and Settings has the same button; the app keeps its window while it reconnects. The app's menu bar item has the same switch ("Start at login"). While tasks are running, the daemon keeps the Mac from sleeping (`caffeinate`), and it lets go as soon as the crew is idle.

`crewbots routines` lists the routines with their next run, `routines run standup` runs one now, and `routines add`
copies in a starter one. `crewbots budgets` shows each bot's use against its budget and where the plans' limits
stand, and `release <task>` lets a held task go ahead. Edited role cards, team files, skills and routines count
within 30 seconds, with nothing to reload.

`crewbots doctor` checks what the crew stands on: the CLIs and their sign-in, the daemon and the login service, the
database's integrity, and what's taking room on the disk, with how to fix or clean up each thing it finds (it
changes nothing itself). The daemon starts a fresh `daemon.log` past 10 MB and keeps the last three.

`crewbots tasks` lists everything as a tree, `watch` follows a task and everything it delegates, and `cancel` stops one. A turn the daemon's stopping cut off (or one waiting its turn) carries on by itself once it starts again: the bot is told it was interrupted and picks up where it left off, a held task stays held, and one you cancelled stays cancelled. If that turn is cut off too, it waits for your message. `crewbots run` skips the crew and the daemon entirely and sends one prompt through one backend.

## The desktop app

The app is a [Tauri](https://tauri.app) shell (Rust) around a React UI. You need Rust (`rustup`) and Node.

```bash
cd app && npm install
```

```bash
npm run dev
```

The dev server needs node 22 (the long-term-support release): on node 26 Vite's dev server hangs at start, though
`vite build` works.

To install it, build the `.app` and copy it to Applications:

```bash
npx tauri build --bundles app
```

```bash
cp -R src-tauri/target/release/bundle/macos/crewbots.app /Applications/
```

Or do all of it, and more, with one command: it runs the checks, builds and installs the app, restarts the daemon once
no turn is running, and relaunches the app (what you'd typed is kept). `--daemon` skips the app, for changes that
don't touch it.

```bash
scripts/release
```

The installed app runs the daemon from this checkout, the path it was built in, so keep the checkout where it is (or
set `CREWBOTS_PROJECT`). To work on the UI in a plain browser, run `npm run vite` and open http://localhost:5173.
Outside Tauri, the UI runs on a built-in demo crew (`src/preview/`).

The app works like a messaging app:
- **Panes resize:** drag the edge between the sidebar, a chat, its profile or thread, and code review's columns; the
  widths are kept, double-click an edge to reset it, and dragging the sidebar narrow collapses it to its rail.
- **Sidebar:** your conversations, most recent first, each bot with its own face, the last message and an unread
  dot, and the group chats. Above them, work that needs you (merges, questions, holds). Search filters them.
  Collapsed, it stays as a rail of icons and faces, and its button stays where it is, beside the window's buttons,
  on one line with them and the headers' titles.
- **A chat:** the bot's replies as bubbles, its tool use folded into one line, and its threads as cards that open
  beside the chat, with the thread's own conversation, its changes, its reviews and a Merge button.
- **Profile:** beside a chat, the bot's status, routines, recent threads, the files its threads produced, and its setup.
- **Group chats:** messages with who said them, @mention suggestions as you type, and who's replying. Beside them,
  the group's members (who leads, who's replying) and the threads it has opened. A new group starts with who's in
  it and a few ways to start.
- **Unsent text is kept:** what you've typed in any message box, with the files you've attached, and a review's
  summary stay put through a reload or a relaunch, per chat, thread and pull request, until you send them.
- **Attachments:** attach images and files to any message, with +, by pasting, or by dropping them on the box. They're
  kept in the crew's `attachments/` folder, show under your message, and the bot gets their paths with it: Claude
  reads images and PDFs itself, and Codex views images.
- **Commands:** typing / in any message box offers the app's own commands; Tab or Enter runs one without sending
  anything. `/usage` shows what's left on your Claude and Codex plans, above the box. `/status` shows the chat's model
  and effort, its session, how full its context is, and its plan's limits.
- **The mic** dictates into any message box with macOS's own speech recognizer, on this Mac when it can, across
  pauses and past the recognizer's one-minute limit. The first time, macOS asks to allow the microphone and speech
  recognition.
- **Code review**, above the chats (its count is what's assigned to you): pull requests that ask you for a review,
  are assigned to you, are yours, or ask your teams (asked for only when you open that list), read with your `gh`
  login. What GitHub said last shows at
  once while the app asks again. Each has its summary, checks and diff. Review runs the PR reviewer, an agent
  of its own (not one of your bots), on each model you choose in Settings (by default Claude Sonnet and Codex's
  default model, both at high effort). Each reviews the PR on its own; then the first, as the lead, checks their
  findings against the code, merges them into one review and drafts its line comments (`draft_comment`, on lines
  the diff shows), which appear in the diff beside yours. The review is a conversation you can carry on, and the
  reviewers' own threads are a click away. Nothing is posted to GitHub by a reviewer. You add, edit and delete drafts, and Submit review sends your review, approving, requesting changes or
  commenting, with the drafts, under your name: the one write to GitHub, and only from the app with your token. No
  reviewer can submit, and none can post a comment anywhere. The diff has line numbers, and changed images show
  before and after. A reviewer is told only which PR and what you'd like looked at, and works in the whole
  repository checked out at the PR's head, from crewbots' own blobless clone (or beside your copy, if you point the
  repo at one in Settings). It asks for the PR's description, files, checks, linked issues and discussion
  (`pr_overview`) and its diff (`pr_diff`), reads the code itself and searches the web for anything outside the
  repo. Reviews are static: nothing is run.
- **The crew menu**, at the bottom left:
  - **Crew:** your bots, and an editor for each: its name and face, title and description, backend, model and
    reasoning effort (from the lists the CLIs themselves report), access, network, MCP servers (a new bot starts with
    all of the crew's), budget, and its role card's text. Edit from a bot's profile opens it over the chat. Saving rewrites the card after checking it loads, and the
    crew uses it from the next turn. New bots start here too.
  - **Activity:** what the bots hand each other, live: work they delegate, reviews they ask for and questions, as
    chains under the chat or thread they came from, what's in flight, and who works with whom this week.
  - **Tools:** the crew's MCP servers and the skills bots can use. Add a server from your other apps (Claude Code,
    Claude's desktop app, Codex), from the official MCP registry, or by hand; Check starts it and lists its tools.
    Each bot gets the servers you choose in its editor.
  - **Routines:** turn them on and off, run them, edit when and what (schedules as "weekdays at 9:00", or cron),
    and add new ones or the starters.
  - **Analytics:** how much of each limit is left, in the CLIs' own terms (Claude's session and weeks, Codex's windows
    and any credits) and when each resets. Then the crew's own use over the last year: its tokens by day, a column a
    week, for Claude, Codex or both; totals, the peak day, the longest turn and streaks; Claude against Codex; the
    top models and bots; and each bot against its budget.
  - **Settings:** start at login, the crew's folder, pausing, GitHub, and code review's local copies.
- **Menu bar and notifications:** a bot's reply or a message in a group chat (unless you muted it) notifies you when the window isn't in
  front, and so do merges to approve, questions and holds.

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

And the app's own logic (Markdown, diffs and find, mentions, commands, the sidebar's order), with Vitest:

```bash
cd app && npm test
```

No test calls a model, and none touches your own folders: each test runs with a home folder of its own, and the run fails if anything in your real `~/.crewbots` or `~/.claude` changed. Backend tests replay real CLI output (`tests/fixtures/`) through a fake CLI. Runner, delegation and API tests use an in-process fake backend whose scripted bots call the same methods the MCP tools reach.

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
  usage.py           the crew's own use, by day, plan, model and bot, for the analytics page
  plans.py           how much of each plan's limits is used, asked of the Claude and Codex CLIs
  store/             tasks and events, in SQLite, with full-text search: a part per area (tasks, events, rooms,
                     search, code review's drafts, routines), and db.py with the layout and numbered migrations
  runner/            runs tasks: core.py (turns, retries) with sessions, rooms, delegation, reviews, work (handoffs,
                     merges, notes) and holds (budgets, pausing, restarting)
  api/               the daemon: HTTP + WebSocket, user and run tokens, host checks
    app.py           who may call it, and how errors answer
    routes/          one module per area: tasks, chats, bots, crew, routines, mcp, reviews, usage, events, ...
    schemas/         the models, by area, which the app's types are generated from
    daemon.py        serve(): the server, its lifespan, daemon.json
  mcp_server.py      the crewbots tools each bot gets (handoff, escalate, review; delegate, tasks, message, propose_merge)
  worktree.py        one git worktree per task
  paths.py           where crewbots keeps things, worked out when asked
  prompts.py         the prompts, read from prompts/*.md, or the crew's own copies
  prompts/           what crewbots tells the models, as markdown
  cli.py             the `crewbots` command
  templates/crew/    the starter crew: bots, team files, skills
tests/               one file per module, plus the fakes and fixtures
app/                 the desktop app: src-tauri/ is the Rust shell, src/ the React UI, src/preview/ the demo crew
docs/                the design, and example role cards
scripts/release      checks, builds, installs and restarts everything, safely
```
