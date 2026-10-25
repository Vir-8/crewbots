---
name: swarm
description: Fan out parallel workers over separate slices of one job (packages, files, checks, questions), then return one consolidated report. Use for broad coverage, sweeps, audits, or "check every X".
---

# Swarm

A crewbots take on [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s `swarm`.

## 1. Frame

State the done condition and the report you will return. Split the job into slices that don't overlap, one per worker: a package, a directory, a list of files, a question. Write one goal per slice that stands alone: the slice, how to check it, and what to report. Reports use `PASS`, `ISSUES` or `BLOCKED`, with evidence. A worker that finds problems lists every one it can prove, not just the first.

Keep within the crew's delegation limit. If there are more slices than that, group them.

## 2. Fan out

Delegate every slice in the same turn, then end your turn. Read-only checks can go to reviewing bots. Anything that writes goes to a bot that owns writing, each in its own worktree.

## 3. Aggregate

When the results arrive, every slice needs a result. A missing or unclear one gets one retry with `message`. After that it is a gap, and a gap is not a pass. Don't paste raw worker output.

## Report

One table (slice, result, one-line evidence), then every issue in a line each, then any gaps.
