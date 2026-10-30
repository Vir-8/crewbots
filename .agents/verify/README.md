# Verifying crewbots

Tests prove units. This proves the change itself, in the running thing, and leaves evidence someone else can check
without taking your word for it. Never hand back "tests pass" alone for a change someone can see or call.

`scripts/verify` does the starting and stopping. How to reach and drive each feature is in its own file here, listed
under [The features](#the-features). Any agent can follow and keep this: it's plain files, and AGENTS.md points here.

## Launch

Start only what the change needs, always in the background through `scripts/verify`. Never run `npm run vite` or
`crewbots serve` in the foreground: they don't return, and your turn waits on them.

- **The app's screens:** `scripts/verify preview` prints `url=`, `pid=` and `log=`. It runs the real UI on a demo crew
  (`app/src/preview/`), with no daemon behind it, ready in about a second. Run `(cd app && npm ci)` once in a fresh
  checkout first.
- **The daemon:** `scripts/verify daemon` prints `url=` (ending in `/v1`), `token=`, `home=`, `pid=` and `log=`. It's a
  crew of its own, a starter crew, in a temp HOME, so nothing of the user's is touched. Its bots can't reach a model:
  use it for routes, rooms, tasks and the store. A live check with real models is the user's or the lead's to run
  (see [daemon.md](daemon.md)).
- **WebKit:** the real app is a WKWebView, and some bugs exist only there (hover kept on a moved element, scroll
  clamped mid-update). `scripts/verify webkit URL --click SELECTOR --js FILE` opens the page in WebKit, clicks with real
  mouse events, runs your script, and prints what it returns as JSON. It opens a window on screen for a few seconds.
  **It can't run in a sandbox like a bot's** (no write to `~/Library/Caches`, no Launch Services): WebKit's helper
  processes don't start. `doctor` says so, and `webkit` gives up after 30 seconds (`--timeout`) with one line saying
  what it waited for. Treat that as unreachable and say so; the user or lead runs it outside the sandbox.

## Doctor

Run `scripts/verify doctor` first. It says what's missing (tools, `app/node_modules`) and whether this shell can
listen on a local port. It also loads an empty page in WebKit and fails if that can't be done (see Launch). In a fresh worktree run
`(cd app && npm ci --prefer-offline)` once, which works in a bot's sandbox. If it can't, as in some sandboxes, say so in your handoff rather than skipping the check
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
command, and the script's file if you wrote one. The commands that prove it and exit 0 when it works (a test file,
`uv run ruff check src tests`, a `scripts/verify webkit … --js` check) go in `checks` as well: crewbots reruns them in
a clean copy before any reviewer starts.

## Cleanup

`scripts/verify stop PID [HOME]` for each thing you started; a scratch daemon's HOME is removed with it. Stop only what
you started. Keep the evidence files (screenshots, scripts) where your handoff points.

## The features

One file per feature. Each file has four sections:
- **Sub-features**: what the feature includes.
- **How to get to it**: from the user's side.
- **Driving it**: with `scripts/verify` and the browser tools.
- **Gotchas**: what trips agents up.

Handles are given as CSS selectors, titles or visible text, as checked against the design preview.

Last walked: 2026-10-30 at 868dcde16a2d (`maintain-verification` sets this to the date and commit of each full pass).

- [chat.md](chat.md): a bot's chat with the user: messages, folded working, cards, the header.
- [group-chats.md](group-chats.md): group chats: posting, mentions, who answers, the group's panel.
- [composer.md](composer.md): the message box: growing, sending, slash commands, attachments, the mic.
- [work-and-threads.md](work-and-threads.md): work cards, the thread panel, reviews, merge, drop and push cards.
- [sidebar.md](sidebar.md): the sidebar: chats, work that needs the user, search, the crew menu, the rail.
- [pages.md](pages.md): Crew, Projects, Activity, Tools, Routines, Analytics, Settings, and the update banner.
- [code-review.md](code-review.md): code review: the inbox, a pull request, its diff and the review. Frozen.
- [daemon.md](daemon.md): the daemon's API on a scratch crew, and what needs a live check.

## Keeping this true

When a feature's handles, journeys or gotchas differ from its file, fix the file in the same change. A new feature gets
a file here, listed above. The crew's verification routine runs `maintain-verification` over it: every feature read
from source and driven in the preview or a scratch daemon, drift fixed, regressions reported, and "Last walked" set.
