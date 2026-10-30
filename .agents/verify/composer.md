# The message box

## Sub-features
- **Typing:** the box grows with what's typed, up to 240px, measured on a hidden copy of the box. The conversation keeps
  its distance from the bottom as the box changes height.
- **Sending:** ↩ sends and ⇧↩ adds a line. A draft is kept per chat across a relaunch.
- **Slash commands:** `/` opens the list; `/usage` and `/status` are among them.
- **@mentions:** a picker in group chats.
- **Attachments:** pick, paste or drop images and files. They show as chips.
- **The mic:** dictation through macOS speech recognition, through a Swift helper in the real app.

## How to get to it
At the bottom of every chat, group chat and thread panel.

## Driving it with scripts/verify
- The box is `.main .composer textarea`. Its buttons are "Attach images or files (or paste, or drop them here)",
  "Dictate" and "Send".
- To type as a person does, set the value with the native setter and fire `input`, a key at a time:
  `Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(area, text);
  area.dispatchEvent(new Event('input', { bubbles: true }))`, then wait two animation frames.
- The bottom gap is `thread.getBoundingClientRect().bottom - lastBlock.getBoundingClientRect().bottom`, with
  `thread = .main .thread` and `lastBlock = thread.lastElementChild`. It should stay the same while you type: in the
  preview it stays 0 as the box goes 26px → 47 → 91 → 156 → 240px (offsetHeight, one to ten lines) and stops at 240.

## Gotchas
- **Check scrolling and sizing in WebKit too** (`scripts/verify webkit`). On 2026-10-29 a box measured by setting it to
  `auto` pushed messages under it in WebKit only, one key at a time. Chromium never showed it.
- The mic can't be checked in the preview or a sandbox. Say so rather than claiming it works.
