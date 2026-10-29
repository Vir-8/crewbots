# Group chats

## Sub-features
- **Posting:** your messages, and bots' replies as `[name]: text`.
- **@mentions:**
  - a mention wakes that bot;
  - `@everyone` wakes all of them;
  - a message with no mention goes to the lead.
- **The bots stopping:** after a room's reply limit, "The bots stopped after N replies", with Continue.
- **The group's panel** ("About this group"):
  - rename;
  - members (a chat with each);
  - mute;
  - archive;
  - delete.
- **New group chat:** a dialog with title, members and lead.
- **Work handed out in the room:** reported there by the bot that did it, @mentioning whoever handed it out.

## How to get to it
- A group's row in the sidebar (in the demo, "Review desk", "Naming the export", and the crew room, shown as "+2").
- "New group chat" sits at the top of the sidebar and in the crew menu.

## Driving it with scripts/verify
- Open a group by its sidebar row, as in chat.md.
- The panel toggle is `.main header [title="About this group"]`. The panel's buttons have titles: "Rename the group",
  "Mute notifications", "Archive", "Delete…", and "Chat with <bot>".
- The message box's placeholder is "Message <group title>".
- Who answers, and room reports, are the daemon's job: check them on a scratch daemon (daemon.md) with
  `POST /rooms/{id}/messages`, then read the room's messages.

## Gotchas
- In the preview, nobody answers in a group: replies are scripted demo data.
- A group with no messages shows a welcome rather than a conversation.
