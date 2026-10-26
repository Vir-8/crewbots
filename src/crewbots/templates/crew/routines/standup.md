---
description: A short summary each weekday morning of what the crew did, and what needs you.
bot: chief
schedule: "0 9 * * 1-5"   # cron, in your time zone: weekdays at 9:00
---
Write the standup for what the crew did since {{since}}, from the record below. Three short parts:

1. Needs you: questions waiting on the user, work ready to merge, anything held on a budget or a plan limit. Leave
   this part out if there's nothing in it.
2. Done: what finished, and where it is now (merged, ready to merge, in review).
3. In progress: what's still going, and who owns it.

This is a report: don't start or delegate any work. If nothing happened, say so in one line.

# What happened

{{activity}}
