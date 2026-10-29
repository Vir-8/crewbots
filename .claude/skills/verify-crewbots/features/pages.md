# Pages

## Sub-features
- **Crew:** each bot's role card (model, effort, access, network, MCP servers, skills), editable in place; adding a bot;
  the chief and coordinators.
- **Projects:** folders the crew may work in, their CI commands, and isolation.
- **Activity:** who asked or handed what to whom, live.
- **Landed:** what merged, the latest first, with its handback's evidence and the verdicts of the merged version; a
  revert for each. Opening it clears the sidebar's count; rows that were new when it opened keep a blue edge.
- **Tools:** MCP servers: add, import, check.
- **Routines:** schedules and triggers, and their runs.
- **Analytics:** usage by day, plan, model, bot and effort, with cache hit rate.
- **Settings:** time zone, auto-merge, stall minutes, updates.
- **The update banner and dialog:** "A new version of crewbots is ready".

## How to get to it
The "Your crew" menu at the bottom of the sidebar, or ⌘K. Landed has its own row under Code review.

## Driving it with scripts/verify
- Open the menu with `.side-row.crew-button`, then click the item by its text.
- The page is in `.main`; its title is the `h1`.
- `?update` in the preview URL shows the update banner.
- Revert asks with `window.confirm`: replace it in `browser_evaluate` (`window.confirm = () => true`) before clicking.
  The demo's revert succeeds without touching git; a real one needs a scratch daemon with a merged task.

## Gotchas
- The demo has fixed analytics and plans. Shapes are worth checking; numbers aren't.
- Settings that change the daemon (auto-merge, stall minutes) need a scratch daemon to check that they took effect.
