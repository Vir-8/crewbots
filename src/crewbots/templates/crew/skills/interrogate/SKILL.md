---
name: interrogate
description: Multi-model adversarial review of a change before the user sees it. Use when work changes code, when a design is contested, or when asked to review, stress-test or "tear apart" something.
---

# Interrogate

A crewbots take on [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s `interrogate`. The signal comes from **model diversity**: reviewers on different backends miss different things. A finding two models raise independently is worth far more than one that only a single model raises.

## 1. Scope and intent

Name the work to review: usually a task you delegated, identified by its id. In one paragraph, state what the change is meant to do, from the goal and the owner's handoff. If you can't state the intent, ask the owner with `message` before going further. Code work comes back with its evidence (what the owner ran and what it showed); work without it goes back for it before any review.

## 2. Fan out

Delegate the same review to every reviewing bot in the crew on a different backend (for example the reviewer on Claude and the critic on Codex), in the same turn, each with `review_of` set to the work's task id. Give each one the same goal:

- the intent paragraph
- what to look at: each reviewer works in a throwaway copy of the work as it stands (the branch, with its uncommitted changes and new files), where it can run the checks and the change itself without touching the work
- the owner's evidence, to rerun what matters rather than take its word. crewbots reruns the checks it was handed back with before the reviewers start, and tells them, so don't ask for those again
- that it should report every problem it can prove, each with its location, a failure scenario and its proof (the command it ran and what it showed), and say plainly when it finds nothing

If the crew has only one reviewing bot, run it once and say in your report that the review was single-model.

Each reviewer ends with the `review` tool, so its verdict and findings come back to you structured, along with the round status crewbots keeps.

Then end your turn.

## 3. Judge

When the reviews arrive, merge findings that describe the same problem, and note which reviewers raised each one. You are the lead reviewer, not a neutral aggregator. Put every finding in one bucket:

- **Act on**: real problems with correctness, security or maintainability. They would block a pull request.
- **Consider**: legitimate, but possibly not worth fixing now. The user should see them.
- **Noted**: valid but not actionable at this stage.
- **Dismissed**: wrong, nitpicky, or missing context. Give a one-line reason.

A finding without proof is shown as not reproduced and can't fail the work on its own: check it yourself, or ask the owner, before it goes in Act on.

## 4. Act

- **Act on findings exist:** tell the owner with `message` which findings to fix, and which you dismissed and why. A failed review starts the owner's next round in a fresh session, from a brief of the goal, its last handoff and the findings, so say what to do now rather than retelling the history. When the fix comes back with fresh evidence, run a new round: the same reviewers, with `review_of` set again. crewbots counts the rounds, and team/rules.md says when they stop; then report the best version and what is still open.
- **All reviewers passed the current version:** call `propose_merge(task_id, summary)`. The summary covers what changed, how it was verified, and anything in Consider. The user merges, or, with auto-merge on, crewbots merges it locally (never pushing) and it shows on the Landed page with its evidence and verdicts, where the user can revert it.
- **A reviewer failed it only on findings you dismissed:** say why in your report to the user, and don't propose the merge yourself. The user can still merge it.

## Report

The intent, the reviewers and their backends, the buckets, and where the reviewers agreed and disagreed.
