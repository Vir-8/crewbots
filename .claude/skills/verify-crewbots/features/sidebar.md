# The sidebar

## Sub-features
- **Chats:** bots and group chats, most recent first, each with its last message and an unread dot. Pinned chats come
  first; archived ones are under "Archived".
- **Work that needs the user:** permission asks, merges, held work (`.need-row`, with the kind as a class).
- **Search:** the "Search" box filters the list.
- **"Your crew" menu** (`.side-row.crew-button`):
  - New group chat;
  - Crew, Projects, Activity, Tools, Routines, Analytics, Settings;
  - Pause the crew.
- **Code review** (`.side-row.nav-row`).
- **The rail:** collapse with `button[title="Collapse the sidebar"]` (`.side-toggle`, fixed beside the window's
  buttons); drag the edge (separator "Resize the sidebar") to resize. Dragging it narrow collapses it.

## How to get to it
Always on the left.

## Driving it with scripts/verify
- Rows are `.sidebar .chat-row`. The open one has the class `on`.
- Menus are `.menu`, holding buttons with visible text.
- Mute or pin a chat from its row's menu (right-click), or the group panel.

## Gotchas
- **Toggles that move when clicked:** WebKit keeps them `:hover` until the pointer moves. A toggle's on state must not
  be a class that React rewrites on the same click (it drops `settled`). Use `aria-pressed`, and check with
  `scripts/verify webkit --click`.
