---
description: A Friday look back at the week's corrections and review findings, turning mistakes that came back into checks.
bot: chief
schedule: "0 16 * * 5"   # Fridays at 16:00
---
Run the weekly retro, over what the crew did since {{since}} (below). Look for patterns, not one-offs:

- a mistake the user corrected, or a review found, more than once, by any bot
- work that took several review rounds, failed, or was held
- a routine that ran without anything to show for it, or a bot asked to do work outside its role

For each pattern, propose one change, at most five, strongest first:

1. **A check**, when the mistake is in code: a test, lint rule or CI step in the project that fails on the mistake. Name
   the project, the tasks where it happened, and what the check runs. A rule the crew has to remember gets forgotten;
   a check doesn't.
2. **A rule** in team/standards.md, when no check can catch it, saying who enforces it (which reviewer looks for it).
3. **An edit to a bot's role card, a skill or a routine**, with the file and the exact text to add or replace.

The user decides. When they say yes to a check, delegate it to an engineer: the check, the past mistake it has to catch,
and for evidence, the check failing on the mistake (its old commit, or a copy of the old code) and passing now. Then it's
reviewed and merged like any work. Edit nothing yourself.

# What went wrong

{{corrections}}

# The week

{{activity}}
