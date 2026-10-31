# Pages

## Sub-features
- **Crew:** each bot's role card (model, effort, access, network, MCP servers, skills), editable in place; adding a bot.
  A coordinator's profile says it delegates; the app has no control for who is chief or a coordinator.
- **Projects:** folders the crew may work in, each with its folder, a description and where tasks work (isolation). CI commands aren't set here.
- **Activity:** who asked or handed what to whom, live.
- **Landed:** what merged, the latest first, with its handback's evidence and the verdicts of the merged version; a
  revert for each. Opening it clears the sidebar's count; rows that were new when it opened keep a blue edge.
- **Tools:** MCP servers: add, import, check.
- **Routines:** schedules and triggers, and their runs.
- **Analytics:** usage by day, plan, model, bot and effort, with cache hit rate.
- **Settings:** start at login, auto-merge, time zone, crew folder, pause, GitHub, the code-review reviewers and local copies. Updates are the banner, not a setting.
- **The update banner and dialog:** "A new version of crewbots is ready".

## How to get to it
The "Your crew" menu at the bottom of the sidebar, or ⌘K. Landed has its own row under Code review.

## Driving it with scripts/verify
- Open the menu with `.side-row.crew-button`, then click the item by its text.
- Projects: a row opens its sheet; "Where tasks work" is a `.segmented` of "The folder itself" and "A worktree each",
  and the row's second line ends with the one in use. The demo's `glider` is a Unity project,
  worked in its folder itself. In a scratch daemon, adding a Unity project (a folder with
  `ProjectSettings/ProjectVersion.txt`) puts `verify-unity` in `GET /v1/skills` once the next tick reloads the crew.
- The page is in `.main`; its title is the `h1`.
- A project's row is a `.list-row`, which opens a `.sheet` with "Choose…", the `.segmented` buttons, Cancel and Save.
- Settings switches are `[role=switch]` with `aria-label`s "Start at login", "Merge approved work automatically",
  "Pause the crew"; the time zone is `input[aria-label="Time zone"]`.
- `?update` in the preview URL shows the update banner `.crew-banner`: "A new version of crewbots is installed",
  "Restart to update", and `button[title="Hide until something changes"]`, which removes it.
- Revert asks with `window.confirm`: replace it in `browser_evaluate` (`window.confirm = () => true`) before clicking.
  The demo's revert succeeds without touching git; a real one needs a scratch daemon with a merged task.

## Gotchas
- Antigravity in the bot editor uses the account-advertised model list. In the preview, select Antigravity and
  check Gemini 3.8 Flash and its thinking levels; the proactive switch and custom context window are hidden.
  Analytics includes Antigravity token totals but directs quota checks to Antigravity itself.
- The demo has fixed analytics and plans. Shapes are worth checking; numbers aren't.
- Settings that change the daemon (auto-merge, time zone) need a scratch daemon to check that they took effect.
