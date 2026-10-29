# crewbots

A crew of always-on AI agents that you message like teammates. Each bot, a chief of staff, an engineering lead and its engineers, a reviewer, or whatever you add such as a video editor working in DaVinci Resolve, has one ongoing chat with you. Real work goes into threads they open beside the chat, each writing bot in its own git worktree. Bots talk to each other in group chats, run routines on their own, and you hear back when there's something to review or decide.

The agents run on the official **Claude Code** and **Codex** CLIs, so they use the Claude and ChatGPT plans you already have. Claude Code is driven through the **Claude Agent SDK**, and Codex through **`codex app-server`**, the two tools' own interfaces for programs. crewbots is the layer above them: bots, tasks, worktrees, delegation, rules and the app. The plan is in [docs/DESIGN.md](docs/DESIGN.md), and the architecture in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## How it works

- **A crew is a folder.** `bots/` has one role card per bot, `team/` has the files every bot reads (mission, standards, protocol, rules), and `skills/` has the skills bots can use.
- **The chief** is stored in the crew's settings, independent of role-card order. Existing crews retain their current chief when the daemon first starts with this version. To change it, the user can call `PUT /v1/crew/chief` with `{"chief": "bot-handle"}`. If that bot is missing, chief-only actions stop and `crewbots doctor` reports it. The chief is always a coordinator; other coordinators are stored once from legacy `can_delegate` role cards and can be changed by the user with `PUT /v1/crew/coordinators` and `{"coordinators": ["bot-handle"]}`.
- **A role card** is Markdown with frontmatter:
  - The frontmatter is what the harness enforces: backend, model, access level, workspace, and the bot's own MCP servers.
  - The body is the card itself: its mission, what it owns, what it does *not* own, and what done means.
  - See [docs/examples/video-editor.md](docs/examples/video-editor.md).
- **Chats.** Each bot has one ongoing chat with you. It never resets: the bot's memory carries it from day to day, and a long one is compacted in place. A scheduled routine posts into its bot's chat too, as a message from the routine.
- **Threads.** For real work, a bot opens a thread (`start_thread`) beside the chat it's in, works it in parallel, and the result comes back to the chat. Any bot can also hand a thread to another (`delegate`). Threads are where worktrees, reviews and merges happen.
- **Group chats.** You and some bots, with one bot as the lead. A message that mentions no one goes to the lead, who answers it, hands it to the teammate best placed for it, or leaves it to whoever already has it. Your @mention brings that bot in and gets a reply. `@everyone` brings them all in but obliges no one to answer; an explicit named mention still gets a reply. Bots @mention each other the same way, but can stay silent if they have nothing to add; there is no reply limit by default. A group chat can set a limit; when it is reached, **Continue** lets the bots carry on. A group chat's panel renames it, changes who's in it and who leads, sets its reply limit, mutes it, and archives it (kept, folded away at the foot of the list, until you say something there) or deletes it (the work it handed out stays, reporting to each bot's own chat). The crew's own group chat can't be archived or deleted, and bots new to the crew join it. Every crew has a "Crew" group chat with all its bots, and a bot can speak there, or in any group chat it's in (by its title or id), from anywhere with `post`. Group chats a crew should have from the start are files in its `rooms/` folder (a title, a lead, its bots, and what it's for, which its bots are told); each is made once, when the daemon starts, and again after a reset. Work handed out in a group chat is talked about there: the handover, questions and answers, fix requests, handbacks and review verdicts (with their findings) are posted whole by whichever bot said them, without waking anyone, so you (and any bot in the room) can follow it and weigh in; the app folds a long one after a few lines. What needs you comes to you in the chief's chat, not the room: from a group chat or a thread, the chief tells you there whenever it has something for you (`tell_user`, with a notification when you're needed now, outside quiet hours), and asks a decision there as a question card (`escalate`); its chat hears what it told you. The starter crew's is Engineering: the chief hands goals to the engineering lead there, and the lead's handovers to the four engineers, their handbacks, and the reviewer's and critic's verdicts show there. A bot can open a group chat of its own with `open_group` (up to eight bots) and bring a teammate into one it's in with `add_to_group`; it shows in the app at once.
- **Files from bots.** A bot shows you what it made (a render, a chart, a PDF, a page, a recording) with `share`: the files are copied into the crew's `attachments/` and appear under its message, in a chat, a thread or a group chat. It can share only from its own folders: the task's worktree or workspace, its own folder, the crew's attachments and its sessions' temp folders. A file it only names in its reply (a render, a PDF, or a page or note it wrote in that turn) shows under the reply too, where it is, when it's in those folders; the code it changed doesn't, since the diff shows that. Images its tools return (a picture it read, a screenshot from Blender, GIMP or Resolve, an image Codex generated) show under its steps, kept once each in `attachments/` rather than in the database. Pictures show inline and enlarge on a click, video and audio play in place, a PDF shows its first page, an HTML page is drawn in a sandbox with its scripts off until you turn them on, and text and code show their first lines, lightly coloured. Open hands a document or media file to its own app; anything that could run, such as a script, is only shown in Finder.
- **Proposed routines.** Ask a bot for something recurring and it proposes a routine (`propose_routine`), which waits, switched off, on the Routines page until you choose Turn on and test; its first run happens then, for you to watch. After a week with nothing from you, routines and check-ins pause until you write again.
- **Check-ins.** Give a bot a checklist in `crew/bots/<bot>/checkin.md` (`- every 2h: pull requests waiting on me`, and optionally `Active hours: 9-18`) and it looks now and then, in the background, when it's idle and there's spare plan capacity, telling its chat only what's worth your attention. `crewbots checkin <bot>` runs one now. What it raises comes as an FYI in the chief's morning digest, a question at a natural break, or, for what can't wait, at once with a notification: at most three a day, and never between 22:00 and 08:00. 👍 or 👎 on one teaches the bot what's worth raising. A check-in may also start a piece of low-risk work on its own (a draft, research, a branch in its own worktree), shown in the chat as "Started on its own" with a Stop button: at most three a day, and never for a bot whose card says `proactive: false`. Items can wait for events instead of a clock (`- on commit acme-web: what changed`), and bots can set standing intents (`remind_on`: "tell me when CI on main passes"); events are new commits and branches in your workspaces, changed files in watched folders, review requests, checks failing or passing, and threads finishing (`crewbots events`, `crewbots intents`).
- **A task** is the unit underneath: a chat, a bot's seat in a group chat, or a thread.
  - Each task has exactly one owner bot and its own backend session, so a follow-up message picks up where the bot left off.
  - A task by a writing bot in a git repo gets its own worktree, on the branch `crewbots/<task id>`. A writing bot with no folder of its own works in `<crew>/work/<bot>`, never in the crew's folder, whose role cards, rules and routines are yours.
  - Every event is stored, so you can read the whole transcript later.
- **Delegation.** Any bot hands a piece of work to the teammate best suited to it through crewbots' own MCP tools:
  - `delegate`, `tasks` and `message` are every bot's, as are `handoff` and `escalate`. Coordinators propose and withdraw merges and schedule their next check; the chief also adds bots (`add_bot`) and proposes pushes.
  - Delegated tasks run in parallel, each as its own bot with its own access. The delegating bot ends its turn instead
    of waiting, and when none of its tasks are still running, their handoffs and results arrive as its next message.
  - `ask(bot, question)` asks a teammate a quick question instead: the teammate answers read-only, with no worktree,
    starts nothing from it, and the answer comes back the same way. The app shows it as a card, "Asked Critic: …
    Answer: …".
  - An escalation reaches it at once.
  - The harness enforces the rules:
    - never to yourself, nor to the chief, nor back to a bot already in the chain (a circle)
    - a chain at most three delegations deep (`crewbots serve --max-delegation-depth` changes it)
    - at most four unfinished delegated tasks per bot at once, and twelve from one task
    - no delegating past a budget or a reached plan limit, the delegating bot's or the teammate's
    - a bot can only touch tasks it delegated
  - Each run's MCP server holds a token that acts only as that task, and only while the run lasts.
- **Review and merge.**
  - A review is a task delegated with `review_of`, so the reviewer works in the reviewed task's worktree. It ends with the `review` tool: a pass or fail verdict, and findings with locations and failure scenarios.
  - crewbots counts the rounds. A round is one reviewed version of the work, so a pass on an older version never approves newer changes. A later version whose change is still the one reviewed (a turn that answered a question, a commit, a clean rebase) keeps the verdict: each review records its work's change as a `git patch-id`. From the third failed round, the lead sends work back only while its findings narrow to a small, clear fix. After five failed rounds it refuses both another round and another revision, and the chief reports to you instead.
  - Before a review starts, crewbots reruns the checks the work was handed back with (`handoff`'s `checks`: commands that exit 0 when it works) in the review's throwaway copy, inside the sandbox a writing bot's commands get, once per version. If one fails, the round fails with its output as proof and no reviewer's turn is spent; if they all pass, the reviewers are told and needn't rerun them. This needs the Codex CLI, whose sandbox it uses; without it the reviewers run what matters, as before.
  - Work nobody has moved on for a while is chased: its delegator is reminded twice, a quiet `stall_minutes` apart, with the next step for each piece (have it reviewed, send back a failed round's findings, propose it, answer a question), and then the chief hears of it. For work handed out in a group chat, both are posted there by crewbots.
  - Work that passes every review of its current version can be proposed with `propose_merge`, which marks it `[ready to merge]`. You merge it in the app or with `crewbots merge <task>`. In Settings, you can opt in to **Merge approved work automatically**; then a proposal merges locally as soon as every review passes. The merge commits the worktree, squashes its branch into your checkout's current branch as one commit whose message is the proposal summary, then removes the worktree. A failed automatic merge remains proposed with the reason returned to the chief. Automatic merges appear in the thread and Activity; undo them with a normal git revert. Pushing and deploying remain yours.
  - It refuses to merge into a checkout with uncommitted changes, and aborts on a conflict, so nothing changes unless the merge is clean.
- **Prompts are files.** Everything crewbots itself tells the models is markdown in `src/crewbots/prompts/`: how to
  work in the crew (`crew-tools.md`) and delegate (`delegation.md`), a chat's and a group chat's setting, the memory
  guide, the briefing, retry and resume turns, the code review prompts (`review/`), and each crew tool's description
  (`tools/<name>.md`). Put a file at the same path under the crew's `prompts/` folder to replace one for your crew
  (`~/.crewbots/crew/prompts/review/lead.md`); delete it to go back. `{name}` placeholders are filled in, and an edit
  counts from the next turn.
- **Skills** are [Agent Skills](https://agentskills.io) folders (a `SKILL.md` each). A role card lists the ones its bot gets, and the bot sees each skill's name, description and path, then reads the `SKILL.md` when a task matches.
  - The starter crew ships `interrogate`, `arena` and `swarm`: crew versions of [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s multi-agent skills. They use real model diversity, a Claude reviewer and a Codex critic. It also ships `verify-unity`, for proving a change to any Unity project in the open Editor through the Unity MCP (doctor, a clean compile, tests, play mode, measurements and screenshots, cleanup), with each project's own map in its `verify/` folder: `create-verification` writes the map (a README and a file per feature, as pstack's feature maps are) and `maintain-verification` keeps it honest, from source and driven live, on the crew's verification routine.
  - Whole skill packs can be dropped in with `crewbots skills add`, and are namespaced (`pstack:tdd`).
- **Memory.** Every bot can `remember` a fact for later tasks (its own, or `shared` with the crew), `forget` one, and `recall` what the crew said or decided before.
  - Notes are files in `crew/memory/<bot>/notes/`, listed in each bot's instructions, so you can read, edit or delete them.
  - Around them: a short `CORE.md` pinned in every session, a daily log (a line for each finished task; a new session starts with the last two days), and each task's state, which the bot keeps with `update_state` and a fresh session of the task starts from. `recall` searches the logs and notes too.
  - Overnight, while the crew is idle, each bot consolidates its memory: it reads what it logged and noted, and crewbots applies what it decides (add, update, supersede, delete or share notes, and rewrite `CORE.md`), then commits the memory folder, its own git repository, so any change can be undone. `crewbots memory consolidate` runs it now.
  - `recall` searches the whole record with SQLite FTS5, and so does `GET /v1/search?q=`.
  - A conversation that fills 65% of its context window is kept in shape at the end of a turn: the bot sets down the
    task's state and a few lines for today's log, then the CLI compacts the conversation in place, and the next turn
    is told its instructions and state again. One that sat idle over an hour, past the prompt cache, carries on in a
    fresh session from memory instead (flushed ahead of time while the cache was warm), as does one that couldn't
    be compacted. The window is the role card's `context_window` (200K tokens unless it says otherwise, up to `1m`,
    or `auto` for the model's whole window): the CLI compacts within it too. `/status` shows how full it is.
  - Bots never use the CLIs' own memory: Claude Code's auto memory and Codex's memories are off for every bot, so
    nothing a bot learns lands beside your own Claude or Codex memory.
- **Routines** are work the crew starts on its own: Markdown files in `routines/`, each naming a bot, a goal, and when.
  - A `schedule` is cron in your time zone. A routine missed while the Mac slept runs once when it wakes, never once
    per miss, and one whose last run is still going is skipped that time.
  - `on: github.review_requested` or `on: github.ci_failed` starts it when a pull request in its `repo` asks you for a
    review, or one of yours fails its checks. The daemon asks GitHub every five minutes with your `gh` login, so
    nothing listens on the internet. A trigger only starts on what's new after it's added, and once per pushed version.
  - The goal can use what the run is about: `{{activity}}` (what the crew did since the last run), `{{corrections}}`
    (corrections bots noted and review findings since then), `{{pr.diff}}`,
    `{{pr.failures}}` (failed checks with the end of their logs), and more (`src/crewbots/routines.py`).
  - A new crew comes with a weekday standup and a Friday retro, which turns mistakes that came back into checks and
    proposes edits to the crew's own files, plus two
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
    - writing bots only write in their task's folder, and in the package caches crewbots keeps for them
      (`~/.crewbots/caches`, uv's and npm's, the crew's and not yours), so they can install a project's dependencies
      and run its tests
    - nobody pushes, merges or publishes
    - a bot has no network unless its role card says `network: true`, or lists the hosts
      (`network: [pypi.org, files.pythonhosted.org, "*.npmjs.org"]`). That covers web search and fetching pages, for
      any bot, and a writing bot's shell commands. A list needs Claude, which checks each page's host: Codex only
      switches the network on or off, and a web search can lead anywhere, so a list allows pages but not search.
    - writing bots' shells never get your logins: `gh` sees an empty config, git has no credential helper and offers
      no SSH key, and there's no SSH agent. Your git name and email stay.
    - no bot reads what's private, short of full access: your keys and logins (`~/.ssh`, `~/.aws`, `gh`'s and the
      CLIs' sign-ins, configs holding MCP servers' keys) and crewbots' own (the daemon's admin token, `mcp.json`, the
      database, the logs). Both CLIs' sandboxes keep commands out of them, and Claude's file tools are refused them.
    - work a bot starts on its own (a check-in, a thread from one) gets none of its MCP servers, only crewbots' own.
  - Other bots' working state is isolated only in sandboxed or policy-checked sessions; `Access.FULL` can read it.
  - **How it's enforced:** Claude asks on every tool call. Codex's sandbox is the boundary, and every request to leave it is declined. Writing bots' shell commands are OS-sandboxed on both.
  - **Claude's tools run inside the daemon.**
  - **Interrupts:** a cancelled or timed-out Claude turn is interrupted, not killed, so its session stays warm.
  - Bots get crewbots' MCP servers and the ones their role card names (`mcp: [blender, gimp]`), from the crew's own
    list in `mcp.json`, never your personal ones. On Claude that's `--strict-mcp-config`. On Codex, which has no such
    switch, every server, plugin and app from your config is turned off by name. A server is a program the CLI
    starts, or a URL (streamable HTTP, or SSE on Claude).
  - A server that drives one running app, such as a Unity Editor's, can be marked `"one_at_a_time": true` in
    `mcp.json` (http servers only). Bots then reach it through the daemon, which lets one task's tool calls through
    at a time for each project (tasks on different projects drive different Editors): another's are answered that it's busy, and by whom, and the first one turned away is told when
    it's free. A task keeps it while its turn runs, until five minutes after its last call. A task in a worktree of
    its own doesn't wait: Unity opens a folder in one Editor only, so the Editor opened on its worktree is its own.
  - Code work isn't handed back with comments that excuse or defer (TODO, FIXME, HACK, workaround, "for now",
    review feedback in the code): the cause gets fixed, or what's unfinished goes in the handoff's `open`. A comment
    stating an outside constraint with its issue link stays. The standards say the rest, for the reviewer.
  - Work done in a project's folder itself is proposed like any other: once approved, crewbots commits just the files
    it handed back as its output (with their Unity .meta files) on the folder's branch, leaving everything else in
    progress there uncommitted. It shows on the Landed page, and the chief proposes the push as usual. Revert there
    leaves other uncommitted work alone, and refuses, changing nothing, only when it would touch a file with
    uncommitted changes.
  - A task in a worktree of a Unity project gets its Editor with `open_unity_editor`: crewbots starts the version the
    project names (no Unity Hub, no screen control), turns on MCP for Unity's "Auto-Start on Editor Load" so it
    connects by itself, tells the task once it has, and closes it before the worktree goes or after 30 idle minutes.
    At most two are open at once. A fresh worktree of a small project opened and connected in 68 seconds, again in 10.
  - **Skills:** every bot sees the crew's skills and your global ones (`~/.claude/skills`, `~/.agents/skills` and
    `~/.codex/skills`), by name and description, and reads a skill's `SKILL.md` when a task matches it, the way Claude
    Code does. Those synced from claude.ai (documents, slides, your morning brief) reach only a bot whose card names
    them. A role card's `skills:` can name only some, `all`, or `none`: each one listed costs every turn a line.
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

`init` creates the starter crew in `~/.crewbots/crew`: a chief of staff, an engineering lead with four engineers (Codex), a reviewer (Claude) and a critic (Codex), and an Engineering group chat for them. Edit its role cards and team files to make it yours. In another terminal, give the chief a goal and follow the whole tree of work:

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
stand, and `release <task>` lets a held task go ahead. `crewbots log` lists what bots did to each other:
delegations, reviews asked for, questions, messages, group chat posts, and tool calls crewbots refused. Edited role cards, team files, skills and routines count
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

macOS keeps what you allow the app (the microphone, speech recognition, Documents) for the identity it's signed with,
and a build signed only ad hoc is a new app to it each time. So that it asks once rather than after every update, make
a signing certificate once: in Keychain Access, Certificate Assistant > Create a Certificate, named
`crewbots local signing`, Identity Type "Self Signed Root", Certificate Type "Code Signing". Every release then signs
the app with it (the first asks to use the key: Always Allow), and you allow the permissions one last time.

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
- **Commands:** typing / in any message box offers the app's own commands; Tab or Enter shows one above the box
  without sending anything, and Enter again runs it. `/usage` shows what's left on your Claude and Codex plans.
  `/status` shows the chat's model and effort, its session, how full its context is, and its plan's limits.
  `/new <goal>` starts a thread for the bot beside its chat; `/group <name>` starts a group chat with it; `/remember
  <note>` gives it a note to keep; `/stop` stops what's running there; `/pause` and `/resume` pause the crew.
- **Pinned chats** stay at the top of the sidebar in your order, above the rest by recent activity: right-click a chat
  to pin or unpin it, drag a pinned one (or drop another among them) to move it, or ⌥↑ ⌥↓ on it. The daemon keeps the
  order, so a reinstalled app has it too.
- **⌘K** goes anywhere: type a few letters of any chat, group, bot, thread, pull request or page, or run a command
  (a new group chat, pause or resume the crew, usage, status, a bot's card, the sidebar). What you picked lately comes
  first.
- **Find:** ⌘F finds in the chat or group chat you're in, or in the thread beside it after a click there; ↩ and ⌘G
  step through the matches, ⇧ steps back. It counts what the messages say, not what's drawn, so a long chat's
  older messages are found too.
- **Updates:** crewbots → Check for Updates… in the menu bar (or ⌘K) checks the checkout, and GitHub when it comes in
  as a fast-forward, says it's up to date or lists what's new, and Update and Restart brings it in: the crew's daemon
  runs the checks and builds the app (`scripts/release`, in `~/.crewbots/build`), then the app installs the build
  over itself, the daemon restarts once no bot is working, and the app restarts. It checks on its own every half hour
  and says so in the banner. It waits while the checkout has uncommitted changes. Settings… is there too (⌘,).
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

## Browser tools

Every bot has a daemon-owned headless Google Chrome browser, started on first use. `browser_open(url)` opens
an HTTP(S) page; `browser_screenshot(width=1400, height=860, theme="light", full_page=false)` returns a PNG the
model can see and saves it as `browser-<theme>-<id>.png` in the task's folder. Use `theme="dark"` to emulate
`prefers-color-scheme: dark`. `browser_click(selector)` clicks a CSS selector, `browser_type(selector, text, key)`
types or presses a named key, `browser_evaluate(expression)` inspects the page with JavaScript, and
`browser_console(clear=false)` reads recent messages and errors. Open a page before using the other tools.

Chrome runs outside the bot's CLI sandbox with a separate disposable profile under `~/.crewbots/browser/`.
Each task has its own context and cookies. A context closes when its turn finishes, is cancelled, or has had no
successful browser action for five minutes. Each task has its own idle timer; refusals don't extend it. Chrome
exits and clears its profile when no contexts remain. On startup, an exclusive profile lock protects live browser
owners. The daemon only stops a recorded orphan when its PID, kernel start time and user match, and only removes
profiles whose recorded process has exited. It logs and leaves unverifiable or older unrecorded profiles alone.
A custom profile root, including the manual smoke check, never runs the orphan sweep. Chrome must be installed at
`/Applications/Google Chrome.app`; no browser package is downloaded.

CDP uses `--remote-debugging-pipe` on inherited file descriptors 3 and 4. There is no debugging TCP listener for
bots or other local clients to connect to. The pipe transport uses Python's standard library; no dependency was added.

Localhost is always reachable. Other hosts follow the bot's network setting (including host lists and the user's
network grants). Each task's proxy requires its own random credential, supplied by the daemon only for that
proxy's CDP authentication challenge. Bots never receive the credential, and it isn't forwarded to websites or
included in diagnostics. The proxy checks connections, including WebSockets; interception checks redirects and
subresources. A changed network policy closes the old context before accepting or refusing the next action.
Downloads and file uploads are disabled; popups, workers and out-of-process frames are closed in this first
version. Child targets that cannot be closed remain paused; safely resuming and inspecting out-of-process frames
and workers is left for the later browser pane. Use the page's own tab for UI checks. Browser actions are logged
and do not grant permission to send, publish, spend or change accounts. The user's approvals still apply.

The session registry in `browser.py` holds each task's context, CDP session, URL and title. A later desktop pane can
use those sessions for a screencast and user input; this version adds no pane or live browser API. For a manual
check without restarting the daemon, start `.claude/launch.json`'s `app-preview` and run
`uv run python scripts/check-browser.py` from an unsandboxed terminal. It calls the daemon's tool implementation
with throwaway state, and saves both themes and session details under `browser-evidence/` in the checkout.
The script logs its interpreter, resolved preview addresses and progress. On failure it preserves `chrome.stderr.log`,
`proxy.json` (including the actual upstream address) and `cdp.json` (commands, attached sessions and paused requests)
before cleaning up Chrome. If different previews listen on IPv4 and IPv6, pass `--url http://127.0.0.1:5173` or
`--url 'http://[::1]:5173'` to select one explicitly.

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
  templates/crew/    the starter crew: bots, team files, skills, routines, group chats
tests/               one file per module, plus the fakes and fixtures
app/                 the desktop app: src-tauri/ is the Rust shell, src/ the React UI, src/preview/ the demo crew
docs/                the design, and example role cards
scripts/release      checks, builds, installs and restarts everything, safely
```
