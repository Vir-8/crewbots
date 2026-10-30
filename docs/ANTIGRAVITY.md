# Antigravity subscriptions

The `antigravity` backend talks ACP over stdio to Google's official `antigravity-acp` runtime.
It does not wrap `agy --print`, call Google's private model endpoints, or read, copy, or proxy account tokens.
The runtime signs in and stores its own credentials. API-key and gateway environment variables are removed from
its environment so this backend cannot silently switch to API billing.

## Setup

On macOS:

```sh
uv run crewbots antigravity install
uv run crewbots antigravity login
uv run crewbots antigravity models
```

The installer downloads the pinned, tested Google release from the official ACP registry and checks its SHA-256.
It installs `agy_acp_server.par` and its matching `localharness_external` together under
`~/.crewbots/bin/antigravity-acp/1.4.5/`. An existing installation is never overwritten.
Sign-in uses a separate `GEMINI_HOME` at `~/.crewbots/antigravity`; Google's runtime owns the profile and credentials.
The account's available models and thinking levels are discovered through ACP with the client name `crewbots`.
No other client identity is impersonated to obtain additional models.

Choose Antigravity in a bot's **Runs on** control, then select an offered model and thinking level.

`CREWBOTS_ANTIGRAVITY_ACP` can point to an already-installed official binary with its matching helper beside it.
`CREWBOTS_ANTIGRAVITY_HOME` selects a runtime-owned profile, useful for scratch verification without copying credentials.

## Execution and permissions

Each active task keeps an ACP session; follow-ups use that session, and `session/load` restores it after the process
closes. Each requested turn is one `session/prompt`, with no time limit unless the bot sets one. Stopping a turn
sends `session/cancel` and closes the runtime, so the next message reloads the session. Quota errors are shown
without retrying, refreshing accounts, or switching models.

The personal-account ACP runtime does not enable the enterprise terminal sandbox. Crewbots therefore disables its
native tools and exposes workspace operations through `crewbots_workspace` MCP. Reads and edits follow the task's
file permissions; shell commands run in a macOS Seatbelt sandbox with separate read-only and workspace-write scopes,
private credential paths denied, and network off unless granted. Domain lists are not supported for this backend.
Only the task's granted MCP servers and crew tools are passed to ACP. Permission replies use one-time grants only.
Question options are never interpreted as tool approvals. Models use crewbots' existing escalation tools for questions.

Crew tools still provide delegation, messages, memory, task handoff, browser tools and shared-app arbitration.
The adapter preserves streamed tool calls, their results and inline tool images. Assistant text is split into messages
at each tool call, and the last message is the turn's reply.
ACP's per-turn usage is recorded under Antigravity, adding cache reads back into total input and including thinking
tokens in output. Context occupancy is separate from token usage; it is not a subscription-quota percentage.

## Deliberate limits

- Requested work only: no proactive check-ins, scheduled or triggered routines, automatic retry, restart recovery,
  idle memory flush, or extra model turns for compaction. The official runtime manages its own context.
- A message sent during a turn waits for the next turn; ACP steering is not implemented.
- Subscription quota is shown in Antigravity itself. Crewbots does not poll model turns to estimate it.
- ACP 1.4.5 sometimes omits the terminal result of an MCP tool call. Such calls are shown as unverified when the
  turn ends; a successful assistant reply is not used to invent a successful tool result.
- Workspace commands need macOS.
- Code review runs only on Claude or Codex.

## Verification

A live check runs in a scratch daemon, as `.agents/verify/daemon.md` describes, never on a real project or the
user's own bots.

References: [official registry](https://github.com/agentclientprotocol/registry/tree/main/antigravity-acp),
[Google ACP release notes](https://antigravity.google/docs/changelog?tab=extensions),
[ACP protocol](https://agentclientprotocol.com/protocol/session-setup).
