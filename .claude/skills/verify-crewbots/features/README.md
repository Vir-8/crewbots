# crewbots features

One file per feature. Each file has four sections:
- **Sub-features**: what the feature includes.
- **How to get to it**: from the user's side.
- **Driving it**: with `scripts/verify` and the browser tools.
- **Gotchas**: what trips agents up.

Handles are given as CSS selectors, titles or visible text, as checked against the design preview.

Last walked: never (`maintain-verification` sets this to the date and commit of each full pass).

- [chat.md](chat.md): a bot's chat with the user: messages, folded working, cards, the header.
- [group-chats.md](group-chats.md): group chats: posting, mentions, who answers, the group's panel.
- [composer.md](composer.md): the message box: growing, sending, slash commands, attachments, the mic.
- [work-and-threads.md](work-and-threads.md): work cards, the thread panel, reviews, merge, drop and push cards.
- [sidebar.md](sidebar.md): the sidebar: chats, work that needs the user, search, the crew menu, the rail.
- [pages.md](pages.md): Crew, Projects, Activity, Tools, Routines, Analytics, Settings, and the update banner.
- [code-review.md](code-review.md): code review: the inbox, a pull request, its diff and the review. Frozen.
- [daemon.md](daemon.md): the daemon's API on a scratch crew, and what needs a live check.
