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
- The whole API is described by `crewbots openapi`, and typed in `app/src/api-types.ts`.
- Making rooms, posting, and reading state back all work without a model: a bot's turn fails, which is recorded, and
  everything up to it can be checked.
- **Live checks with real models:** a scratch crew with Haiku cards on a spare port, with the real HOME, is the user's
  or the lead's to run, outside a bot's sandbox. Ask for it with `escalate`, saying exactly what to run and what to
  look for.

## Gotchas
- Most daemon behaviour has fakes in `tests/fakes.py` (`ScriptedCrew`, fake sessions). A focused test there is the
  first proof. The scratch daemon is for what crosses the API.
- Never point anything at the user's own daemon (`~/.crewbots/crew`): it's their running crew.
