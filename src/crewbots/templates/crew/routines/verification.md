---
description: Each weekday morning, the verification maps of projects that changed get a pass, so verifying stays quick and true.
bot: eng-lead
schedule: "0 7 * * 1-5"   # weekdays at 7:00, before the day's work
---
Run the crew's verification upkeep, over the projects in your Projects list.

For each, hand one engineer the `maintain-verification` pass on it, as its own piece: projects worked in their folder
itself share the user's open Editor, so give those out one after another; the rest can go at once. The pass itself
skips a project with nothing new since its last walk. A project with no verification map gets `create-verification`
instead, but only one that has had work in the last week.

When they're back, `post` one short summary to the Engineering group chat: a line per project with its outcome, and any
regression found, which you hand out there as new work. Propose the map corrections as usual.
