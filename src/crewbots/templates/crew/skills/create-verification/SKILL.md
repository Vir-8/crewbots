---
name: create-verification
description: Give a project its verification map, the per-project half of verifying it the way a user would (a verify skill for its kind of app, or the project's own method, is the other half). Use when a project has none, before its first real piece of work, or when asked to "map how to verify" something.
---

# Create a project's verification map

A crewbots take on [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s `create-verification-skill`. A verify
skill, where the crew has one, says how to drive a kind of app; the project's map says how to drive this one:
where each feature is, how a user reaches it, how to drive it, and what proves it works. You write it for the next bot,
which reads it cold, mid-task.

## Where it lives

`.agents/verify/` in the project: `.agents/verify/README.md`, and one file per feature, with a line in the project's
AGENTS.md pointing there. They're plain files, so any agent can follow and keep them, and crewbots lets every writing
bot change this folder, though Codex keeps the rest of `.agents/` shut. Never in a skills folder (`.agents/skills/`,
`.claude/skills/`) or another agent's own folder (`.claude/`, `.codex/`, `.cursor/`): each agent loads only its own,
and the CLIs keep bots from writing there. A verify skill already in one moves to `.agents/verify/`, its text becoming
the README. A project with no `.agents/` folder yet: a Codex bot can't make one, so say so in your handback.

## 1. Read the project, not the user

From the code and the project's own docs (AGENTS.md, README, design docs), answer:

- **Surface:** what a user touches: a UI, a CLI, an API, scenes and menus.
- **Run:** how it starts for a check (a dev server, a build, an Editor and a scene), what "ready" means.
- **Drive:** what the harness can do to it (a browser, a command, an MCP server), and the stable handles it can use:
  routes, labels, menu items, object names and paths, component fields. Not coordinates.
- **Observe:** what counts as evidence: values read back, the console, test results, screenshots, files written.
- **Set up states:** seed data, debug or cheat commands, test scenes or menu items that put the app where a feature is.

Ask the user (`escalate`) only what you can't find out yourself.

## 2. Write `.agents/verify/README.md`

- **Run and doctor:** the verify skill it builds on, by name, if there is one, so the next bot loads it. Then what's
  particular to this project: where to start from, what "ready" looks like, errors already in the console that
  aren't anyone's to chase.
- **Setting up states:** the commands, seed data, menu items or test scenes that reach a state quickly.
- **Tests:** which test suites exist and how long they take, or that there are none.
- **Features:** the index, a line each, linking to its file.
- **Last walked:** the date and commit of the last full pass (`maintain-verification` keeps it).

## 3. Seed the features

One file per user-facing feature, the three to five that matter most to start (from routes, screens, menus and docs),
each with these four sections:

- `## Sub-features`: what it is, in parts.
- `## How to get to it (user POV)`: how a user reaches it.
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

The map's files and the AGENTS.md change as your `output`, the feature you proved and what it showed as `evidence`,
and in `open` the features you saw but didn't map yet. Keeping it honest as the project changes is
`maintain-verification`'s job.
