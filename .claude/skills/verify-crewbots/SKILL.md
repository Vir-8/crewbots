---
name: verify-crewbots
description: Prove a crewbots change works in what the user runs (the app's screens, WebKit, a running daemon) and hand back the evidence. Use before handing back any change to app/ or src/, and when reviewing one.
---

# Verify crewbots

Tests prove units. This proves the change itself, in the running thing, and leaves evidence someone else can check
without taking your word for it. Never hand back "tests pass" alone for a change someone can see or call.

`scripts/verify` does the starting and stopping. The features, and how to reach and drive each, are in
[features/](features/README.md).

## Launch

Start only what the change needs, always in the background through `scripts/verify`. Never run `npm run vite` or
`crewbots serve` in the foreground: they don't return, and your turn waits on them.

- **The app's screens:** `scripts/verify preview` prints `url=`, `pid=` and `log=`. It runs the real UI on a demo crew
  (`app/src/preview/`), with no daemon behind it, ready in about a second. Run `(cd app && npm ci)` once in a fresh
  checkout first.
- **The daemon:** `scripts/verify daemon` prints `url=` (ending in `/v1`), `token=`, `home=`, `pid=` and `log=`. It's a
  crew of its own, a starter crew, in a temp HOME, so nothing of the user's is touched. Its bots can't reach a model:
  use it for routes, rooms, tasks and the store. A live check with real models is the user's or the lead's to run
  (see [features/daemon.md](features/daemon.md)).
- **WebKit:** the real app is a WKWebView, and some bugs exist only there (hover kept on a moved element, scroll
  clamped mid-update). `scripts/verify webkit URL --click SELECTOR --js FILE` opens the page in WebKit, clicks with real
  mouse events, runs your script, and prints what it returns as JSON. It opens a window on screen for a few seconds.

## Doctor

Run `scripts/verify doctor` first. It says what's missing (tools, `app/node_modules`) and whether this shell can
listen on a local port. If it can't, as in some sandboxes, say so in your handoff rather than skipping the check
quietly. Before driving the preview, check it shows the demo crew: `browser_open(url)`, then a screenshot shows Roger's
chat with the sidebar on the left.

## Drive

- **Screens:** use the crewbots browser tools on the preview URL: `browser_open`, `browser_click(selector)`,
  `browser_type`, `browser_evaluate(js)` and `browser_screenshot(theme=light|dark)`. Prefer a control's `title` or
  `aria-label`, or its visible text. Many sidebar rows have no accessible name, so find them by their text. Each feature
  file gives its handles.
- **Measure, don't eyeball,** when the bug is about layout or timing. Use `browser_evaluate` (or `--js` in WebKit) for
  sizes, positions and computed styles, sampled across a few animation frames when it's about what happens after a
  click or a key.
- **Do what the user does.** Type a key at a time when typing is the subject; click the real control rather than
  calling the handler; check both themes for anything visual.
- **Daemon:** call its routes with `curl -H "Authorization: Bearer $token" "$url/..."`, then check the result through
  another route.
- **Bugs:** reproduce first, on the code before your change, and keep that output: the failing check is half the
  evidence.

## Evidence

Hand back the real path and the state after it, not a summary:

- **Commands and their output:** the measurement, the curl and its response, the test run.
- **Screenshots:** share them with `share` so they show in the chat. Take a before and an after when the change is
  visible.
- **For a bug:** the failing check before the fix and the same check passing after, word for word.
- **Anything you couldn't check,** and why. A check that came back inconclusive, or ran on the wrong surface (Chromium
  when the bug is WebKit's), is not a pass.

Put it in the handoff's `evidence`. Whoever verifies your work reruns what matters, so make it rerunnable: the exact
command, and the script's file if you wrote one.

## Cleanup

`scripts/verify stop PID [HOME]` for each thing you started; a scratch daemon's HOME is removed with it. Stop only what
you started. Keep the evidence files (screenshots, scripts) where your handoff points.

## Keeping this skill true

When a feature's handles, journeys or gotchas differ from its file, fix the file in the same change. A new feature gets
a file in [features/](features/README.md). The crew's daily check walks every feature file against the preview and
fixes what drifted.
