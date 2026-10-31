# The daemon

## Sub-features
- **Chats, threads, seats and rooms:** the runner, sessions, compaction, steering.
- **Delegation and reviews:** merge, push, drop, and CI before merging.
- **The rest:** routines and triggers, the bot API (`/v1/bot/*`, used by Codex bots' tools), and analytics.

## How to get to it
The app talks to it at `~/.crewbots/crew/daemon.json` (`url`, `token`), on routes under `/v1`.

## Driving it with scripts/verify
- `scripts/verify daemon` gives `url` (ending in `/v1`) and `token`, on a starter crew (chief, eng-lead, engineer-1 to 4,
  reviewer, critic, and a room "Engineering") in a temp HOME.
- Call it with `curl -s -H "Authorization: Bearer $token" "$url/version"`. Other routes include `/bots`, `/chats`,
  `/rooms`, `/tasks?limit=50`, `/events/recent`, `/crew/timezone`, `/usage/summary`, `/budgets`, `/models`, `/landed`,
  `/update`, `/pins`, `/search?q=` and `/routines`. Settings are under `/crew/` (`/settings` and `/analytics` are 404);
  `/crew/merge-settings` is read with GET and changed with `PUT {"auto_merge": true}` (POST is 405).
- Seen working: `POST /rooms` (title, members, lead) makes a room; `POST /rooms/{id}/messages` with `{"text":"@engineer-2
  hello"}` returns 201, the message reads back from `GET /rooms/{id}/messages` and from `/search`, and the mentioned
  bot's task goes `queued`, then `idle` with no reply: it can't reach a model.
- Seen working: `PUT /presets/Everyday` with no body saves the crew's models as they are (`presets.json` in the crew's
  folder); `PUT /bots/models` with `{"bots": {"engineer-1": {"backend": "claude", "model": "sonnet", "effort":
  "high"}, ...}}` rewrites those cards in one go (a bad effort for one bot gives 422 and writes none); `POST
  /presets/Everyday/apply` puts them back, and `GET /presets` says `"in_use": "Everyday"` again.
- The whole API is described by `crewbots openapi`, and typed in `app/src/api-types.ts`.
- Making rooms, posting, and reading state back all work without a model: a bot's turn fails, which is recorded, and
  everything up to it can be checked.
- **Live checks with real models:** a scratch crew with Haiku cards on a spare port, with the real HOME, is the user's
  or the lead's to run, outside a bot's sandbox. Ask for it with `escalate`, saying exactly what to run and what to
  look for.

## Gotchas
- Antigravity uses the official Google ACP runtime (see `docs/ANTIGRAVITY.md`). Run `crewbots antigravity models`
  to discover account-specific models without a model turn. For a user-authorized live check, point only the scratch
  daemon at the runtime-owned signed-in profile with `CREWBOTS_ANTIGRAVITY_HOME` and the official binary with
  `CREWBOTS_ANTIGRAVITY_ACP`; never copy tokens. Verify workspace MCP, crew MCP, a follow-up and separate usage totals.
- Most daemon behaviour has fakes in `tests/fakes.py` (`ScriptedCrew`, fake sessions). A focused test there is the
  first proof. The scratch daemon is for what crosses the API.
- Never point anything at the user's own daemon (`~/.crewbots/crew`): it's their running crew.
