---
description: Works out why checks failed on one of your pull requests, and what would fix it.
bot: chief
on: github.ci_failed   # checks failed on a pull request you opened
repo: owner/name
workspace: ~/code/name
enabled: false
budget: 400000
---
Checks failed on {{pr.repo}}#{{pr.number}} ({{pr.title}}), branch {{pr.branch}}: {{pr.url}}

Find the cause from the failures below. If the fix is in this repo, delegate it to the engineering lead with the
failure, to have fixed and reviewed; the fix has to land on {{pr.branch}}, so say that in your summary. If it's flaky,
or outside the code (a secret, a runner, a service that was down), say so and change nothing.

{{pr.failures}}
