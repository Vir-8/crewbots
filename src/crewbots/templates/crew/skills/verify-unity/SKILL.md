---
name: verify-unity
description: Prove a change to a Unity project works in the open Unity Editor (it compiles, its tests pass, the scene plays as asked) and hand back the evidence, through the Unity MCP. Use before handing back any change to a Unity project, and when reviewing one.
---

# Verify a Unity change

Tests prove units. This proves the change in the Editor, the way a player meets it, and leaves evidence someone else
can check without taking your word for it. It works through the Unity MCP (the `UnityMCP` server: `manage_editor`,
`run_tests`, `read_console`, `manage_camera`, `manage_scene` and the rest); the `unity-mcp-orchestrator` skill has their
details. "It compiles" alone isn't done.

## How Unity work runs here

- A Unity project (it has `ProjectSettings/ProjectVersion.txt`) is worked on in **branch mode**: in the project's own
  folder, on the task's branch, one task per project at a time. The Editor the user keeps open on that folder sees your
  files, so what you drive through the MCP is your work.
- There's no throwaway copy of the work, so crewbots doesn't rerun `checks` for it, and Unity in batch mode can't open
  a project the Editor has open. Leave `checks` empty: the reviewer reruns your evidence through the MCP.
- Never edit `Library/`, `Temp/`, `Logs/`, `obj/` or `UserSettings/`: Unity makes them.

## 1. Doctor, before anything else (read-only)

1. Read `mcpforunity://instances`. Find the instance whose project is your working directory, and `set_active_instance`
   to it when there's more than one. None at all means the Editor isn't open on the project or isn't connected:
   `escalate` that the user needs to open the project in Unity and start MCP for Unity's session (Window › MCP for
   Unity), then end your turn. Don't start Unity yourself, and don't verify against another project's Editor.
2. Read `mcpforunity://editor/state`: wait while `is_compiling` or `is_domain_reload_pending`, and go on only once it's
   `ready_for_tools`. A branch switch makes Unity reimport, which can take minutes.
3. `read_console(types=["error"])` and note what's already there, so you don't chase errors that aren't yours.
4. Read `mcpforunity://project/info` (render pipeline, Input System, UI packages), `mcpforunity://tests` (which test
   assemblies exist), and the project's `VERIFY.md` (below).

## 2. After every change: a clean compile

Your file tools write behind the Editor's back, so `refresh_unity`, wait for `is_compiling` to be false (the MCP can
drop for a moment during a domain reload: read the state again), then `read_console(types=["error", "warning"])`. With
a compile error anywhere, nothing else you check counts: fix it first.

## 3. Drive it

- **Tests.** `run_tests(mode="EditMode")`, and `mode="PlayMode"` for runtime behaviour, then `get_test_job` until it's
  done. A bug fix gets a test that fails before the fix (run it and keep that output) and passes after, when the
  project has a test assembly to put it in. Logic you can pull out of a MonoBehaviour is worth an EditMode test.
- **Play.** Note the open scene, open the one the change lives in (`manage_scene`), `manage_editor(action="play")`, let
  it run, and check what was asked: the objects and values involved (`find_gameobjects`, component properties), and
  `read_console` for exceptions. Then `manage_editor(action="stop")`, always, even after a failure: play mode keeps
  nothing you change in it, and leaving it running blocks the user.
- **Measure, don't eyeball.** Positions, counts, timings and states from components (or `execute_code` reading them)
  beat a screenshot that looks right. The MCP can't press keys: reach input-driven behaviour through a test (the Input
  System's test fixture) or by calling what the input calls.
- **See it.** For anything visible, a screenshot: a positioned one (`view_target`, `view_position`) saves no file;
  others save into `Assets/Screenshots/`, so delete what yours wrote, with its `.meta`. Images the MCP returns show
  under your steps in the app; `share` any file you keep.

## 4. Evidence

In the handoff's `evidence`, each item something the reviewer can rerun through the MCP:

- the console after the last compile: no errors, and any warnings your change added;
- the tests: which ran, how many passed, and for a fix the failing run before it;
- what play showed: the scene, what you did, the values you measured, and the screenshot;
- what you couldn't check, and why (no test assembly, needs a build or a device, needs a person playing).

A check that was inconclusive, or ran in the wrong place (another project's Editor, a stale compile), is not a pass.

## 5. Cleanup

Play mode stopped; the scene the user had open, open again; scenes and prefabs saved only when your change meant to
change them. `git status` shows only what the change meant to change, and every new asset has its `.meta`.

## The project's VERIFY.md

What you learn about verifying a project goes in `VERIFY.md` at its root, which the next bot reads in step 4: the
scenes and how to reach each feature (scene, objects, the method or menu item that sets it up), test assemblies and how
long they take, debug or cheat commands that set up a state, and gotchas. Start it the first time you verify in a
project that has none, and correct what you find wrong in it, in the same change.

When reviewing, do steps 1 to 3 yourself against the handback's evidence, read-only: tests, play, measurements and
positioned screenshots change nothing. Report what you find rather than fixing it.
