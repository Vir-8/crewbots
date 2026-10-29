# Work cards and threads

## Sub-features
- **Work cards in a chat:**
  - the bot, title and status (Working, Done, Has a question, Failed, Stopped);
  - the line of what it's doing;
  - review chips with their verdicts, and the round;
  - the Merge button, "Merged", or "Dropped" with the reason on hover.
- **The thread panel:** a task's whole conversation beside the chat, with its own message box and Close.
- **Lines in a thread:**
  - delegations;
  - verdicts;
  - "proposed it for merging";
  - "withdrew";
  - "dropped it: …";
  - merges.
- **Push cards:** one per project. A newer card replaces the open one ("Replaced by a newer card below").
- **Replace-main cards.**

## How to get to it
Work cards sit in the chat where the work was started. Click one to open its thread in the panel.

## Driving it with scripts/verify
- The cards are `.main .work`, with the title in `.work-title`, the status in `.work-status`, and review chips in
  `.review-chip`. Click a card to open the panel: `.panel`, whose header holds the bot and the task's title.
- Push cards are `.push-card`. A replaced one has the class `replaced` and no Push button.
- Thread lines come from `app/src/components/threadBlocks.ts` (`build`). For a new kind of event, a unit test there is
  quicker than the preview, and a demo event in `app/src/preview/demo/` shows it.

## Gotchas
- Merging, pushing and dropping really happen in the daemon. Check them on a scratch daemon with a git repo, or with
  `tests/` (test_drop.py, test_push.py, test_merge_ci.py). The preview only fakes the replies.
