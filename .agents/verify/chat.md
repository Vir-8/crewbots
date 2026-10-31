# A bot's chat

## Sub-features
- The conversation:
  - your messages and the bot's replies, with time breaks;
  - working between two messages folded into one line ("Kept 2 notes · 1 step");
  - quiet lines (retried tries, compactions, drops);
  - links a bot writes, `[label](target)`: a web address opens in the browser, a path (absolute, from ~ or file://)
    is shown in Finder, and so is a relative one, found among the files shown under the message, else in the folder
    its task works in (a thread's worktree or workspace, a chat's bot's workspace). A path Finder can't find greys,
    and its title says it wasn't found. Anything else is only its label. A label may hold one level of brackets, and
    its code and bold are drawn;
  - files a reply, or what the chief told you from elsewhere ("From Design"), names, shown under it.
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
- A bot's links are `a.text-link`, titled with the path Finder shows or the web address, and `a.text-link.missing`
  once Finder couldn't find it. Roger's last message, "From Design", has two path links, a web link and the two
  screenshots they name. The one before it has relative links to the retro and plan shown under it, one with brackets
  in its label and one with bold and code. In the thread of "Add truncate(s, n) to text.py, with tests…" (open "Add
  truncate(s, n)…", then its card in the panel), the engineer's links go through its worktree, and "scratch notes"
  isn't there.
- The app can't know a chat's own folder when its bot names no workspace (the daemon's ~/Documents/Crewbots/<bot>),
  or which task posted to a group chat: their relative links resolve only to files shown under the message.
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
