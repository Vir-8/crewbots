# A bot's chat

## Sub-features
- The conversation:
  - your messages and the bot's replies, with time breaks;
  - working between two messages folded into one line ("Kept 2 notes · 1 step");
  - quiet lines (retried tries, compactions, drops).
- Cards in the conversation:
  - work cards (see work-and-threads.md);
  - questions with choices;
  - permission asks;
  - Push and Replace-main cards.
- The header:
  - the bot's face and name;
  - a status line ("Working on …");
  - the Profile toggle, which opens the bot's profile beside the chat.
- Find (⌘F) within the chat.

## How to get to it
Click a bot's row in the sidebar (Roger, Critic, Reviewer, Engineer in the demo). The app opens on the chat that was
open last.

## Driving it with scripts/verify
- `scripts/verify preview`, then `browser_open(url)`.
- Open a chat: `browser_evaluate` clicks `[...document.querySelectorAll('.sidebar .chat-row')]`, the one whose
  `innerText` includes the bot's name.
- The conversation is `.main .thread`, with its blocks in `.main .thread-col`.
- The header is `.main header`. The Profile toggle is `header .icon-button[title="Profile"]`; its state is
  `aria-pressed`.
- Demo URL switches:
  - `?task=<id>` opens a task;
  - `?still` turns off the demo's live activity, for comparing screenshots.

## Gotchas
- The demo crew is in `app/src/preview/demo/`. A state the change needs (a failed try, a dropped task) may have to be
  added there first, and then it's part of the change.
- The conversation draws its latest 80 blocks, and more as you scroll up to them.
- Chat rows have no accessible name: find them by text.
