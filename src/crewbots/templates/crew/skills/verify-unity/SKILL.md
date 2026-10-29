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

- A Unity project (it has `ProjectSettings/ProjectVersion.txt`) is worked on as the user sets per project. **The
  folder itself**, the default: on whatever branch it's on, beside the user and the other engineers, each on different
  files. The Editor the user keeps open on it sees everyone's work, yours included. **A worktree each**: a checkout of
  your own, so you verify in an Editor of its own: ask for it with `open_unity_editor`. crewbots opens it, tells you
  once it's connected, and closes it when your work is done with.
- The project's Editor is shared: crewbots lets one task at a time make Unity MCP tool calls on it (tasks on other
  projects use theirs), and answers others that
  it's busy, and with whom. Then do what doesn't need it, or end your turn: you're told when it's free. In a worktree of
  your own, the Editor opened on it is yours, and nobody waits. Keep each check short, so
  the others aren't kept waiting. In a shared folder, a compile error in a file you didn't touch is another engineer's:
  don't fix it, say so (`escalate` to the lead) and check again once it's gone.
- Either way there's no throwaway copy for the review, so crewbots doesn't rerun `checks`, and Unity in batch mode
  can't open a project an Editor has open. Leave `checks` empty: the reviewer reruns your evidence through the MCP.
- Never edit `Library/`, `Temp/`, `Logs/`, `obj/` or `UserSettings/`: Unity makes them.

## 1. Doctor, before anything else (read-only)

1. Read `mcpforunity://instances` (listed by project name, as `Name@hash`), `set_active_instance` to yours when
   there's more than one (again after any reconnect: a call answering `instance_selection_required`), and check
   `mcpforunity://project/info`: its `projectRoot` must be your working directory.
   None for your folder: in a worktree, call `open_unity_editor` and end your turn (its first open imports the whole
   project). In the project's folder itself, `escalate` that the user needs to open it in Unity and start MCP for
   Unity's session (Window › MCP for Unity), then end your turn. Don't start Unity any other way, and don't verify
   against another folder's Editor: it isn't your work.
2. Read `mcpforunity://editor/state`: wait while `compilation.is_compiling` or `is_domain_reload_pending` is true, and
   go on only once `advice.ready_for_tools` is. A branch switch makes Unity reimport, which can take minutes.
3. `read_console(types=["error"])` and note what's already there (warnings from packages often show as errors), so you
   don't chase errors that aren't yours.
4. Read `mcpforunity://tests` (whether there are EditMode or PlayMode tests at all), and the project's map: what it says
   about the features you'll touch (below).
5. In the Editor the user has open, `manage_scene(action="get_active")`: while its scene has unsaved changes
   (`isDirty`), don't open another scene or enter play mode, which would stop on a save dialog of theirs. Check what
   you can without them, and say what's waiting.

## 2. After every change: a clean compile

Your file tools write behind the Editor's back, so `refresh_unity`, wait for `is_compiling` to be false (the MCP can
drop for a moment during a domain reload: read the state again), then `read_console(types=["error", "warning"])`. With
a compile error anywhere, nothing else you check counts: fix it first.

## 3. Drive it

- **Tests.** `run_tests(mode="EditMode", include_failed_tests=true)`, and `mode="PlayMode"` for runtime behaviour: it
  returns a `job_id`; call `get_test_job(job_id=...)` until its status isn't `running`, and read `result.summary`
  (total, passed, failed). A run with `total: 0` ran nothing, which proves nothing: say the project has no tests for
  it. A bug fix gets a test that fails before the fix (run it and keep that output) and passes after, when the project
  has a test assembly to put it in. Logic you can pull out of a MonoBehaviour is worth an EditMode test.
- **Play.** Note the open scene, open the one the change lives in (`manage_scene`), `manage_editor(action="play")`, let
  it run, and check what was asked: the objects and values involved (`find_gameobjects`, component properties), and
  `read_console` for exceptions. Then `manage_editor(action="stop")`, always, even after a failure: play mode keeps
  nothing you change in it, and leaving it running blocks the user.
- **Saves are real.** Play mode reads and writes the game's real saves and settings (`Application.persistentDataPath`,
  and PlayerPrefs in `~/Library/Preferences/unity.<company>.<product>.plist`). Before playing, copy them to a fresh
  folder in system temp and check each copy; after, put back what changed and remove what the run created. A game
  that syncs saves to a cloud (Steam Cloud) can upload them mid-run: if its client is running, stop and say so.
- **Code in play mode.** `execute_code` is CodeDom C# 6: no `$""`, `?.`, `out var` or local functions; write
  `UnityEngine.Object.FindFirstObjectByType<T>()`, never a bare `Object`, and read private members by reflection.
  Values kept between calls go in `System.AppDomain.CurrentDomain.SetData`/`GetData`.
- **Frames.** An Editor without focus barely ticks in play mode, often one frame per MCP call, so a write shows a
  frame later (`Rigidbody.position` on the next physics step). To drive it surely, pause and step inside one call:
  `EditorApplication.isPaused = true`, `Time.captureFramerate = 60`, then `EditorApplication.Step()` in a loop.
- **Input.** Queue Input System events from `execute_code` (`InputSystem.QueueStateEvent(Keyboard.current, new
  KeyboardState(Key.W))`, then an empty `KeyboardState` to release; `MouseState` for buttons). Where the Editor drops
  them, call the game's own input handlers instead, and say in the handoff that the bindings weren't exercised.
- **Measure, don't eyeball.** Positions, counts, timings and states from components (or `execute_code` reading them)
  beat a screenshot that looks right. Frame time: vsync caps wall time, so read `FrameTimingManager`'s
  `cpuMainThreadFrameTime`, the median over 1,500 frames after 120 to warm up, from a fixed pose with no input. Runs in
  different Editor sessions differ by milliseconds: compare before and after within one session, and give the spread.
  Stepped frames have no real frame time.
- **See it.** For anything visible, a screenshot: `manage_camera(action="screenshot", include_image=true,
  output_folder="Temp/crewbots-screenshots")`, with `view_target` or `view_position` to frame it. Every screenshot
  saves a file, by default into `Assets/Screenshots/` with `.meta` files that would be committed with the work; under
  `Temp/` it's ignored by git and never imported. The image comes back to you and shows under your steps in the app.
  While play mode is paused or stepped, frames aren't presented and captures come out empty: render the camera into a
  small `RenderTexture` and save that as a PNG under `Temp/` (without the HUD or UI Toolkit). Don't read a full-size
  capture (4K is about 8 MB) back into your context.

## 4. Evidence

In the handoff's `evidence`, each item something the reviewer can rerun through the MCP:

- the console after the last compile: no errors, and any warnings your change added;
- the tests: which ran, how many passed, and for a fix the failing run before it;
- what play showed: the scene, what you did, the values you measured, and the screenshot;
- what you couldn't check, and why (no test assembly, needs a build or a device, needs a person playing).

A check that was inconclusive, or ran in the wrong place (another project's Editor, a stale compile), is not a pass.

## 5. Cleanup

Play mode stopped; the scene the user had open, open again; scenes and prefabs saved only when your change meant to
change them. What your change changed is exactly what you list in the handoff's `output` (in a shared folder,
`git status` shows others' work too), and every new asset has its `.meta`.

## The project's map

How to reach and drive each feature of this game is in its map: `verify/` at the project's root (`README.md`, with where
to start, setting up states, tests and the feature index, and a file per feature), or the project's own verify skill
(`.agents/skills/verify*/SKILL.md`, which its AGENTS.md usually points to). Where the map is more specific than this
skill, follow the map. A project with none needs `create-verification` first: say so in your handback, or do it if the
lead asked. What you find wrong in the map while verifying, correct in the same change; the regular pass over all of it
is `maintain-verification`'s.

When reviewing, do steps 1 to 3 yourself against the handback's evidence, read-only: tests, play, measurements and
screenshots into `Temp/` change nothing in the work. Report what you find rather than fixing it.
