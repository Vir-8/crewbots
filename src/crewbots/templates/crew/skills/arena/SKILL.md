---
name: arena
description: Run several parallel attempts at the same task, pick the strongest as the base, and graft the best parts of the others into it. Use when one attempt at a non-trivial design or change would lock in the wrong shape, or when asked to "arena" something or compare approaches.
---

# Arena

A crewbots take on [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s `arena`. Every candidate works in its own worktree, so the attempts never interfere.

## 1. Frame

Every candidate gets the same goal, so the goal is the contract. Write:

- the artifact each candidate must produce
- 3 to 6 concrete criteria you will judge them by. Keep these to yourself: candidates only see the task.

## 2. Fan out

In one turn, delegate the identical goal two or three times. Use different backends where the crew has more than one bot that can do the work, because model diversity is the point. Ask each candidate to say, in its handoff, which alternatives it considered and why it rejected them. Then end your turn.

If a candidate fails, continue with the rest and say so.

## 3. Judge and pick a base

Delegate one review per candidate to a reviewing bot, with `review_of` set, scoring it against your criteria. Each review runs in a throwaway copy of its candidate, so the reviewer can run it and back each finding with proof. Read every candidate's handoff and the reviews. Pick the base by the criteria, one at a time, not by overall feel. When two are close, prefer the smaller change or the cleaner boundary.

If the candidates converged on one shape, that is strong agreement: say so and keep it. If they diverged wildly, the frame was too vague, so rewrite it and run again. Don't average the differences.

## 4. Graft and verify

Send the base's owner, by `message`, the one or two things worth porting from each losing candidate, with their worktree paths. Then have the result reviewed like any other work (the interrogate skill), and drop each losing candidate with `drop_task`, saying why: its folder goes and its branch stays.

## Report

The base and why, each graft and where it came from, what was rejected, and the verification result. Once every review of the result passes, propose it with `propose_merge`: the user merges it, or, with auto-merge on, it lands locally and shows on the Landed page.
