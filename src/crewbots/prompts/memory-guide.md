# Memory

Your notes, and the crew's, outlast this task. Use them before asking the user something they may already have told
the crew, and read a note's file when it bears on the task.

- `remember(title, body, shared?)`: keep something that will still be true and useful next time: what the user prefers,
  a decision and its reason, a pitfall in this codebase, how something here is done. One fact per note, with a short
  title. Not task progress (that's the handoff) and not anything a file in the repo already says. `shared: true` puts
  it in the crew's notes, for every bot. Remembering the same title again updates that note.
- `forget(name, shared?)`: remove a note that's wrong or out of date.
- `recall(query)`: search everything the crew has done (messages, replies, handoffs, reviews, questions) for what was
  said or decided before. Results carry task ids.
