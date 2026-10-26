---
description: Drafts a review when someone asks you to review a pull request.
bot: reviewer
on: github.review_requested   # someone asks you, not one of your teams, for a review
repo: owner/name              # the repo to watch
workspace: ~/code/name        # your checkout of it, so the reviewer can read the code around the change
enabled: false                # turn it on once repo and workspace are yours
budget: 300000
---
{{pr.author}} asked for your review of {{pr.repo}}#{{pr.number}}: {{pr.title}}
{{pr.url}}

Draft the review the user will post. The diff is below; the working directory is a checkout of the repo (it may not be
on the pull request's branch), for the code around the change. Give a verdict, approve or request changes, and one
finding per real problem: file:line, the problem, and the input or state that goes wrong. Leave out style nits the
linters catch, and don't restate the diff.

```diff
{{pr.diff}}
```
