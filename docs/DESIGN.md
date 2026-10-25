# crewbots: design

A desktop app for running a crew of persistent coding agents. You talk to a lead bot; it hands work to worker bots, each running in its own git worktree, and comes back when there is something to review or decide.

## What we're borrowing

| From | Idea | In crewbots |
|---|---|---|
| ChatGPT Dots | An always-on agent with a standing responsibility that delegates deeper work to Codex | **Bots** keep their role, memory and threads between runs. A bot can start **tasks** that run on a coding backend |
| ChatGPT Dots | Custom Rules: free / ask first / hand back | Per-bot **rules**, enforced by the harness, not by the prompt |
| ChatGPT Dots | Activity View: running and delegated work, steer or stop it | The app's main screen |
| Grok Bot | A manager bot coordinating specialist bots | A **lead** bot with delegation tools |
| Grok Bot | Bots message each other in shared threads | `message_bot` / group threads |
| Grok Bot | Routines and schedules | **Triggers** (cron first, events later) |
| pi | Use the subscriptions you already pay for | Run the official **Claude Code** and **Codex** CLIs headless, so they use their own sign-in |

## How a team works

From [a guide to running a Grok Bot team](https://x.com/eng_khairallah1/status/2113204927405400074). Its point is that the gap between a thousand good pull requests and a thousand bad ones is everything around the model. We build these into the harness rather than leaving them to prompts:

- **Start with three bots.** A **coordinator** plans, assigns, tracks and assembles, and never does the work itself. Two specialists do the work. The fourth bot to add is a **reviewer**, because the bot that writes something should not be the only one that checks it.
- **Role cards.** Each bot has a mission, what it *owns*, what it does *not own*, its tools, and what "done" means. The "does not own" line prevents most of the chaos. Role cards are files, and their tools and access become the CLI flags for that bot.
- **A shared brain in files you own.** A `team/` folder in the repo (`mission.md`, `standards.md`, `roles.md`, `protocol.md`, `rules.md`) that every bot reads. It's inspectable and editable, unlike opaque model memory.
- **Exactly one owner per task.** Only the coordinator assigns or reassigns. The store enforces this, not the prompt.
- **One handoff format.** `HANDOFF: task / from → to / done / output (paths) / open / needs`. The next bot works from the handoff and the files it points to, nothing else. That keeps context clean and every step auditable. In crewbots a handoff is a structured tool call (`handoff(...)`), not free text.
- **Escalation ladder.** The owner tries up to twice, then asks the bot that owns the problem, then the coordinator decides, and only then does it come to you. Anything external or irreversible comes straight to you.
- **Hard stop conditions,** enforced in code: at most 3 review rounds (then you get the best version plus the open objections), at most 2 attempts before escalating, and a usage budget per bot and per task.
- **Rhythm.** Routines such as a daily standup summary, weekly planning that you approve, and a Friday retro that names the one role card or standard to fix.
- **Measure each bot:** approval rate, revision rounds, escalation accuracy, missed handoffs, and usage share. These all come from the store, since every task, handoff and review is recorded there.

## The key decision: the CLIs are the agent loop

We don't write the coding loop ourselves. Claude Code (`claude -p --output-format stream-json`) and Codex (`codex exec --json`) already provide file editing, shell, sandboxing and context management, and both run on your subscription. crewbots is the layer **above** them:

- **Backends** run a CLI as a subprocess and translate its event stream into one common `Event` type. They also resume sessions (`claude --resume`, `codex exec resume`).
- **Orchestration tools** come from an MCP server that crewbots hosts. Each CLI is started with it (`--mcp-config` for Claude, `-c mcp_servers.*` for Codex), so any bot on either backend can call `delegate_task`, `message_bot`, `list_tasks`, `request_approval` and `remember`.
- **Rules** become CLI flags (permission mode, allowed tools, sandbox mode). Anything that needs your sign-off goes through `request_approval`, which blocks until you answer in the app.

This is the same split as the refund harness: the model can only *request*, and code decides what actually happens.

## Pieces

```
desktop shell (later: Tauri or Electron)
        │  WebSocket + HTTP, localhost only
crewbots daemon (Python, asyncio)
  ├── api/          HTTP + WebSocket for the app
  ├── store/        SQLite: bots, threads, messages, tasks, events, approvals
  ├── crew/         bots, the lead, delegation, rules
  ├── tasks/        task runner, git worktrees, merging back
  ├── backends/     claude_cli, codex_cli → common Event stream
  ├── mcp/          the orchestration tools served to every CLI
  └── triggers/     schedules
```

**Core types**
- `Bot`: name, role prompt, backend + model, rules, memory file, workspace (repo path).
- `Thread`: a conversation with one or more bots and you.
- `Task`: a piece of work given to a bot, with a goal, a worktree/branch, status (`queued → running → needs_input → review → merged | failed | cancelled`), the backend session id, and a cost/usage total.
- `Event`: normalized backend output (`text`, `tool_call`, `tool_result`, `usage`, `done`, `error`), stored and streamed live to the app.

## Milestones

1. **Backends** (done). Run one prompt through each CLI in a worktree, normalize the events, resume a session. Tests use a fake CLI script, so none of them call a model.
2. **Daemon and store** (done). Bots loaded from role cards, the `team/` brain in every bot's instructions, tasks with a single owner, events in SQLite, an HTTP/WebSocket API, and the `crewbots` CLI. There is no separate "thread" type: a conversation with a bot is a task, and a follow-up message resumes its session.
3. **Crew** (done). The crewbots MCP server (`delegate`, `tasks`, `message` for coordinators; `handoff`, `escalate` for everyone), handoffs stored as records, reporting back to the coordinator when its work is done, run tokens, and skills, including crew versions of pstack's `interrogate`, `arena` and `swarm`.
   Still to come: a structured review loop that counts rounds in code (the 3-round limit is in the rules and the skill for now), `ask_bot` between workers, and the merge step.
4. **Rhythm.** Bot memory, schedules, routines (standup, planning, retro), budgets and per-bot metrics.
5. **Desktop app.** The Activity view, threads, diff review and approvals.

### Learned in milestone 1

- Without `--strict-mcp-config` and `--setting-sources project,local`, `claude -p` loads the user's personal MCP servers, hooks and plugins. Bots must not inherit those.
- `codex exec resume` takes neither `-s` nor `-C`, so the sandbox is set with `-c sandbox_mode=…` and the directory is the process's working directory.
- Claude's list options (`--allowedTools`, `--mcp-config`) take multiple values and would swallow a prompt passed after them, so both CLIs get the prompt on stdin.
- Read-only access was checked on real runs: Claude's `dontAsk` mode with only the Read, Glob and Grep tools, and Codex's `read-only` sandbox, both refused to write a file.

### Learned in milestone 3

- Codex refuses MCP calls that need approval when its approval policy is `never`, so a headless bot's tools failed until each crew-granted server got `default_tools_approval_mode = "approve"`. The role card granting the server is the approval, which matches Claude's `--allowedTools`.
- mcp 2.x hides an exception's message from the model ("Error executing tool delegate") unless the tool raises `ToolError`. Refusals have to say why, or the bot can't correct course.
- With "don't poll" in its instructions, the chief still called `tasks()` once straight after delegating, just to "wait". The tool description now says it is for looking things up, not for waiting.
- In the first real run, the chief made the design decision itself instead of escalating trivia. It ran interrogate on two backends and read the final code itself before reporting. The Codex critic ran 17 extra edge-case checks that the Claude reviewer didn't.

### Learned in milestone 2

- Codex's `workspace-write` sandbox limits *writes* to the worktree, but it can still *read* anywhere. On a real run the engineer searched every crewbots worktree for an AGENTS.md. Worktrees keep parallel bots from colliding; they are not a security boundary between bots. (The Grok Bot guide says the same: "don't treat separate bots as a security boundary.")
- A reviewer with read-only access can't run tests, and it said so ("tests were not run") instead of claiming they passed. When the review step lands, it should get the engineer's test output, or a way to run tests that can't write.
- Watchers must never see a task go `idle` while a follow-up is waiting to start, or they stop early. A task with queued messages stays `queued` between turns.

## Open questions

- Desktop shell: Tauri (small, Rust) or Electron (simpler with a Python sidecar). This can wait until milestone 5, because the daemon API is the contract.
- Whether bots should share one long-lived app-server process per backend (`codex app-server`, `claude --input-format stream-json`) instead of one process per run. Start with one process per run and measure.
