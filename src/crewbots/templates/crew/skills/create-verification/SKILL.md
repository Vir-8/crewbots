---
name: create-verification
description: Give a project its verification map, the per-project half of verifying it the way a user would (verify-unity, verify-crewbots or the project's own method are the other half). Use when a project has none, before its first real piece of work, or when asked to "map how to verify" something.
---

# Create a project's verification map

A crewbots take on [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s `create-verification-skill`. A verify
skill says how to drive a kind of app (verify-unity for any Unity game); the project's map says how to drive this one:
where each feature is, how a user reaches it, how to drive it, and what proves it works. You write it for the next bot,
which reads it cold, mid-task.

## Where it lives

`verify/` at the project's root (outside `Assets/`, so Unity leaves it alone): `verify/README.md`, and one file per
feature. A project that already keeps a verify skill of its own in the repo (`.claude/skills/verify-*/` with a
`features/` folder, as crewbots does) keeps its map there instead.

## 1. Read the project, not the user

From the code and the project's own docs (AGENTS.md, README, design docs), answer:

- **Surface:** what a user touches: scenes and menus, a UI, a CLI, an API.
- **Run:** how it starts for a check (for Unity: the Editor, which scene, play mode), what "ready" means.
- **Drive:** what the harness can do to it (the Unity MCP, a browser, a command), and the stable handles it can use:
  object names and paths, menu items, component fields, routes, labels. Not coordinates.
- **Observe:** what counts as evidence: values read back, the console, test results, screenshots, files written.
- **Set up states:** debug or cheat commands, test scenes, editor menu items that put the game where a feature is.

Ask the user (`escalate`) only what you can't find out yourself.

## 2. Write `verify/README.md`

- **Run and doctor:** what's particular to this project on top of its verify skill: which scene to start from, what
  "ready" looks like, errors already in the console that aren't anyone's to chase.
- **Setting up states:** the commands, menu items and test scenes that reach a state quickly.
- **Tests:** which test assemblies exist and how long they take, or that there are none.
- **Features:** the index, a line each, linking to its file.
- **Last walked:** the date and commit of the last full pass (`maintain-verification` keeps it).

## 3. Seed the features

One file per user-facing feature, the three to five that matter most to start (from scenes, menus, routes and docs),
each with these four sections:

- `## Sub-features`: what it is, in parts.
- `## How to get to it (user POV)`: how a player or user reaches it.
- `## Driving it with <harness>`: the preconditions, then each step with its real handles and the observable result
  that proves it, ending with the evidence to capture.
- `## Gotchas`: what trips a driver up, and what a passing-looking check can hide.

When the project can't reach a feature's precondition, the step reports that and why, under Gotchas. Never weaken a
journey to make it pass.

## 4. Prove it before handing it back

Follow the map yourself once, end to end, for one feature: run, doctor, drive, capture evidence, clean up, and check the
evidence is still where you said. A map no one has followed is a draft. Fix what failed, and clean up after every
failed try too.

## Hand back

The map's files as your `output`, the feature you proved and what it showed as `evidence`, and in `open` the features
you saw but didn't map yet. Keeping it honest as the project changes is `maintain-verification`'s job.
