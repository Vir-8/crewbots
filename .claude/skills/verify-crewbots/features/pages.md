# Pages

## Sub-features
- **Crew:** each bot's role card (model, effort, access, network, MCP servers, skills), editable in place; adding a bot;
  the chief and coordinators.
- **Projects:** folders the crew may work in, their CI commands, and isolation.
- **Activity:** who asked or handed what to whom, live.
- **Tools:** MCP servers: add, import, check.
- **Routines:** schedules and triggers, and their runs.
- **Analytics:** usage by day, plan, model, bot and effort, with cache hit rate.
- **Settings:** time zone, auto-merge, stall minutes, updates.
- **The update banner and dialog:** "A new version of crewbots is ready".

## How to get to it
The "Your crew" menu at the bottom of the sidebar, or ⌘K.

## Driving it with scripts/verify
- Open the menu with `.side-row.crew-button`, then click the item by its text.
- The page is in `.main`; its title is the `h1`.
- `?update` in the preview URL shows the update banner.

## Gotchas
- The demo has fixed analytics and plans. Shapes are worth checking; numbers aren't.
- Settings that change the daemon (auto-merge, stall minutes) need a scratch daemon to check that they took effect.
