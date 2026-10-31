# crewbots: design

A desktop app for running a crew of persistent coding agents. You talk to a lead bot; it hands work to worker bots, each running in its own git worktree, and comes back when there is something to review or decide.

## What we're borrowing

| From | Idea | In crewbots |
|---|---|---|
| ChatGPT Dots | An always-on agent with a standing responsibility that delegates deeper work to Codex | **Bots** keep their role, memory and threads between runs. A bot can start **tasks** that run on a coding backend |
| ChatGPT Dots | Custom Rules: free / ask first / hand back | Per-bot **rules**, enforced by the harness, not by the prompt |
| ChatGPT Dots | Activity View: running and delegated work, steer or stop it | The app's main screen |
| Grok Bot | A manager bot coordinating specialist bots | A **lead** bot with delegation tools |
| Grok Bot | Bots message each other in shared threads | `message` and group chats |
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
- **Hard stop conditions,** enforced in code: at most 5 review rounds, from the third only while the findings narrow (then you get the best version plus the open objections), at most 2 attempts before escalating, and a usage budget per bot and per task.
- **Rhythm.** Routines such as a daily standup summary, weekly planning that you approve, and a Friday retro that names the one role card or standard to fix.
- **Measure each bot:** approval rate, revision rounds, escalation accuracy, missed handoffs, and usage share. These all come from the store, since every task, handoff and review is recorded there.

## The key decision: the CLIs are the agent loop

We don't write the coding loop ourselves. Claude Code and Codex already provide file editing, shell, sandboxing and context management, and both run on your subscription. crewbots is the layer **above** them:

- **Backends** drive each CLI through its official integration point (the Claude Agent SDK, `codex app-server`) and translate its events into one common `Event` type. They resume a task's session on its next turn.
- **Crew tools** are Python functions in the daemon. Claude gets them in-process; Codex gets them from a small MCP server over stdio that forwards each call to the daemon. Either way a bot can call `delegate`, `ask`, `message`, `handoff`, `escalate` and `remember`, and the daemon decides what each task may do.
- **Rules** become CLI flags (permission mode, allowed tools, sandbox mode). Work that needs more than a role card gives goes through `ask_permission`, and decisions that aren't the bot's go up through `escalate`. Both wait for the answer.

The model can only *request*; code decides what actually happens.

## Learned running the CLIs headless

- Without `--strict-mcp-config` and `--setting-sources project,local`, `claude -p` loads the user's personal MCP servers, hooks and plugins. Bots must not inherit those.
- `codex exec resume` takes neither `-s` nor `-C`, so the sandbox is set with `-c` (crewbots' permissions profile) and the directory is the process's working directory.
- Claude's list options (`--allowedTools`, `--mcp-config`) take multiple values and would swallow a prompt passed after them, so both CLIs get the prompt on stdin.
- Read-only access was checked on real runs: Claude's `dontAsk` mode with only the Read, Glob and Grep tools, and Codex's `read-only` sandbox, both refused to write a file.

## Learned building the daemon and store

- Codex's `workspace-write` sandbox limits *writes* to the worktree, but it can still *read* anywhere. On a real run the engineer searched every crewbots worktree for an AGENTS.md. Worktrees keep parallel bots from colliding; they are not a security boundary between bots. (The Grok Bot guide says the same: "don't treat separate bots as a security boundary.")
- A reviewer with read-only access can't run tests, and it said so ("tests were not run") instead of claiming they passed. When the review step lands, it should get the engineer's test output, or a way to run tests that can't write.
- Watchers must never see a task go `idle` while a follow-up is waiting to start, or they stop early. A task with queued messages stays `queued` between turns.

## Learned building the crew tools

- Codex refuses MCP calls that need approval when its approval policy is `never`, so a headless bot's tools failed until each crew-granted server got `default_tools_approval_mode = "approve"`. The role card granting the server is the approval, which matches Claude's `--allowedTools`.
- mcp 2.x hides an exception's message from the model ("Error executing tool delegate") unless the tool raises `ToolError`. Refusals have to say why, or the bot can't correct course.
- With "don't poll" in its instructions, the chief still called `tasks()` once straight after delegating, just to "wait". The tool description now says it is for looking things up, not for waiting.
- In the first real run, the chief made the design decision itself instead of escalating trivia. It ran interrogate on two backends and read the final code itself before reporting. The Codex critic ran 17 extra edge-case checks that the Claude reviewer didn't.

## Learned building the review loop

- Codex's `workspace-write` sandbox can't commit inside a worktree. A worktree's git metadata lives in the main repo's `.git/worktrees/`, outside the writable root. So bots leave changes uncommitted, and crewbots commits them when the user merges. That is also the right split: the commit and the merge are the user's act.
- Counting a round as "one reviewed version" (the work's finished-run count when review was asked for) makes the limit objective. Two reviewers of the same version are one round, and a revision makes the next review a new round. A pass on version 1 never approves version 2.
- In the live run, both reviewers recorded structured PASS verdicts. The Codex critic ran 610 boundary and property checks. The round status told the chief it could propose, and it did, with a summary covering edge-case decisions and the critic's "consider" notes. The merge commit records who reviewed it and in which round.
