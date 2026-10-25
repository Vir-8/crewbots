---
name: interrogate
description: Multi-model adversarial review of a change before the user sees it. Use when work changes code, when a design is contested, or when asked to review, stress-test or "tear apart" something.
---

# Interrogate

A crewbots take on [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s `interrogate`. The signal comes from **model diversity**: reviewers on different backends miss different things. A finding two models raise independently is worth far more than one that only a single model raises.

## 1. Scope and intent

Name the work to review: usually a task you delegated, identified by its id. In one paragraph, state what the change is meant to do, from the goal and the owner's handoff. If you can't state the intent, ask the owner with `message` before going further.

## 2. Fan out

Delegate the same review to every reviewing bot in the crew on a different backend (for example the reviewer on Claude and the critic on Codex), in the same turn, each with `review_of` set to the work's task id. Give each one the same goal:

- the intent paragraph
- what to look at (the uncommitted changes in its working directory: `git diff` plus new files)
- that it should report every problem it can prove, each with its location and a failure scenario, and say plainly when it finds nothing

If the crew has only one reviewing bot, run it once and say in your report that the review was single-model.

Each reviewer ends with the `review` tool, so its verdict and findings come back to you structured, along with the round status crewbots keeps.

Then end your turn.

## 3. Judge

When the reviews arrive, merge findings that describe the same problem, and note which reviewers raised each one. You are the lead reviewer, not a neutral aggregator. Put every finding in one bucket:

- **Act on**: real problems with correctness, security or maintainability. They would block a pull request.
- **Consider**: legitimate, but possibly not worth fixing now. The user should see them.
- **Noted**: valid but not actionable at this stage.
- **Dismissed**: wrong, nitpicky, or missing context. Give a one-line reason.

## 4. Act

- **Act on findings exist:** send them back to the owner with `message`, as one list. When the fix comes back, run a new round: the same reviewers, with `review_of` set again. crewbots counts the rounds and stops at three. Then report the best version and what is still open to the user.
- **All reviewers passed the current version:** call `propose_merge(task_id, summary)`. The summary covers what changed, how it was verified, and anything in Consider. The user decides whether to merge.
- **A reviewer failed it only on findings you dismissed:** say why in your report to the user, and don't propose the merge yourself. The user can still merge it.

## Report

The intent, the reviewers and their backends, the buckets, and where the reviewers agreed and disagreed.
