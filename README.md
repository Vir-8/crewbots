# crewbots

A local daemon and macOS app that runs a crew of long-lived coding agents on the Claude Code, Codex and Antigravity
runtimes. You message each bot like a teammate. They delegate to each other, work in parallel in their own git
worktrees, review each other's work across models, and come back to you only for a decision or a merge.

It runs on the subscriptions you already have, through each vendor's official interface for programs. No API keys,
no scraped endpoints. crewbots is the layer above: the crew, its tasks and worktrees, the policy every bot runs
under, memory, scheduling, and the app.

## How it works

- **Long-lived sessions.** Each active task holds one session: Claude Code through the Claude Agent SDK, Codex
  through `codex app-server` (JSON-RPC), Antigravity over ACP. Follow-ups go to the warm session. Past 70% of the
  context window, the bot writes down its state, the CLI compacts in place, and the next turn gets its instructions
  and state again. A conversation idle past the prompt cache restarts fresh from memory instead of paying to reload.
- **Crew tools over MCP.** `delegate`, `handoff`, `review`, `escalate`, `remember`, `recall`, `share` and the rest
  are defined once as pydantic models. Claude gets them in process. Codex reaches them through a stdio MCP server
  that calls the daemon. Each run's token acts only as its own task, for as long as the run lasts.
- **Delegation is tasks.** A bot hands work to a teammate as a child task with its own session, access and worktree,
  ends its turn, and is woken with the handoffs when the children finish. The runner limits chain depth and fan-out,
  and refuses cycles.
- **Handoffs carry proof.** A handoff says what's done, what's open, the evidence, and `checks`: commands that exit 0
  when it works. crewbots reruns them in a throwaway copy before a reviewer spends a turn. Reviews are counted per
  version of the work, keyed by `git patch-id`, so a pass never approves later changes. The starter crew reviews
  with a Claude reviewer and a Codex critic, for real model diversity.
- **Merges are yours.** Approved work is squashed onto your checkout's branch only after the repo's own CI steps
  (`.github/workflows/*.yml`) pass on the result. Auto-merge is opt-in. Nothing is ever pushed or published by a bot.
- **One policy on every backend** (`backends/policy.py`). Read-only bots read, and writing bots write only in their
  task's folder, inside each CLI's OS sandbox. Network is off unless the role card allows it, to any host or a list.
  Shells get no git or `gh` credentials. Your keys, the CLIs' sign-ins and the daemon's token can't be read. Text
  from outside (PR titles, diffs, fetched pages) reaches the model marked as untrusted data.
- **Memory is files.** Per-bot notes and a short pinned `CORE.md`, a daily log, and per-task state, all Markdown you
  can edit, plus SQLite FTS5 (word and trigram) over every transcript for `recall`. Overnight, each bot consolidates
  its notes into a git-tracked folder, so every change can be undone. There's no vector store: keyword recall scores
  0.975 hit@1 on `scripts/recall_eval.py`.
- **Always on.** The daemon is a launchd service. Routines run on cron or GitHub events, and check-ins let a bot look
  at a checklist on its own. Self-started work runs only on spare plan capacity. Budgets and plan limits hold a
  task instead of failing it mid-turn.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the decisions behind each of these, and what was measured.

## A crew is a folder

```
~/.crewbots/crew/
  bots/        one role card per bot
  team/        mission, rules, standards and protocol, read by every bot
  skills/      Agent Skills folders (SKILL.md), listed per role card
  routines/    scheduled and triggered work
  rooms/       group chats the crew starts with
  memory/      each bot's notes, and the crew's
```

A role card's frontmatter is what the harness enforces. Its body is the bot's job:

```markdown
---
title: Engineer 1
description: Writes and changes code in a project, with tests, and hands back work that's ready for review.
backend: codex
access: workspace_write
network: true            # or a list of hosts, on Claude
mcp: [blender]           # from the crew's own mcp.json
---
You make code changes that work, are tested, and are easy to review...
```

`crewbots init` writes the starter crew: a chief of staff, an engineering lead, four Codex engineers, a Claude
reviewer and a Codex critic, with an Engineering group chat where their handoffs and verdicts show. The prompts
crewbots itself sends are Markdown in `src/crewbots/prompts/`, and a crew can override any of them.

## Get started

You need macOS, Python 3.12 with [uv](https://docs.astral.sh/uv/), and `claude` and `codex` installed and signed in.

```bash
uv sync
```

```bash
uv run crewbots init
```

```bash
uv run crewbots serve
```

Then give the chief a goal and follow the tree of work it starts:

```bash
uv run crewbots task chief "Add a --verbose flag to the CLI, with tests" --workspace ~/code/myproject
```

`crewbots diff`, `review` and `merge` take the task id. `crewbots doctor` checks the CLIs, the daemon and the
database, and `crewbots --help` lists every command. For Antigravity, see [docs/ANTIGRAVITY.md](docs/ANTIGRAVITY.md).

## The app

A Tauri 2 shell around a React and TypeScript UI, typed from the daemon's OpenAPI schema. It needs Rust and Node.

```bash
cd app && npm install && npm run dev
```

`scripts/release` runs the checks, builds and installs the app, and restarts the daemon once no turn is running.
`crewbots service install` keeps the daemon running at login, with or without the app.

## Working on crewbots

[AGENTS.md](AGENTS.md) has the checks and conventions, and `.agents/verify/` says how to prove a change in the
design preview, WebKit or a scratch daemon. No test calls a model or touches your real `~/.crewbots`: backend tests
replay recorded CLI output through fakes.
