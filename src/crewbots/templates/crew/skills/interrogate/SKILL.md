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

Then end your turn.

## 3. Judge

When the reviews arrive, merge findings that describe the same problem, and note which reviewers raised each one. You are the lead reviewer, not a neutral aggregator. Put every finding in one bucket:

- **Act on**: real problems with correctness, security or maintainability. They would block a pull request.
- **Consider**: legitimate, but possibly not worth fixing now. The user should see them.
- **Noted**: valid but not actionable at this stage.
- **Dismissed**: wrong, nitpicky, or missing context. Give a one-line reason.

## 4. Act

Send the **act on** findings back to the owner with `message`, as one list. Its fix comes back to you; review again only what changed. Stop after three rounds and report what is still open, as team/rules.md says.

## Report

The intent, the reviewers and their backends, the buckets, and where the reviewers agreed and disagreed.
