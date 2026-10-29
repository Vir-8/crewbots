---
name: maintain-verification
description: The regular pass that keeps a project's verification map honest as the project changes - every feature read from source and driven live, the map corrected, and anything actually broken reported rather than papered over. Use when the verification routine hands you a project, or when asked to check or audit a project's verification map.
---

# Maintain a project's verification map

A crewbots take on [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s `maintain-verification-skill`. A map
rots the moment the project changes. This pass covers every feature from source and drives every feature live, without
proving every sentence of every file.

## Outcomes

End with exactly one, and say which:

- **unchanged:** nothing changed in the project since the map's last walk (step 0), so nothing was driven.
- **clean:** every feature was read and driven, and the map was right.
- **changed:** the map needed corrections, proven live, and they're your handback.
- **blocked:** coverage couldn't finish (the Editor wouldn't connect, a feature needs something only the user has), or
  a proven fix couldn't be made safely. Say exactly what blocked it.

## What you may change

Only the map: `verify/` at the project's root, or the project's own verify skill folder if it keeps one (as crewbots
does). Never the project's code. A behaviour the map describes that the project no longer has is either drift (fix
the map) or a regression (report it, and leave the map saying what should happen).

## The pass

0. **Locate, and skip if nothing changed.** Find the map (`verify/README.md`, or the verify skill's
   `features/README.md`). None: stop and say the project needs `create-verification`. Read its "Last walked" line: if
   it names the commit the project is on now (`git log -1 --format=%H`) and no uncommitted work touches the features,
   the outcome is **unchanged**.
1. **Index.** Fix missing, extra, duplicate or dead entries between the index and the feature files.
2. **Source.** For each feature file, read the code behind it (with your CLI's subagents in parallel, if you have
   them): how the feature works now, where it lives (file:line), drift the map shows, and one recipe for checking it
   live. Then sweep the commits since the last walk for user-facing changes the map doesn't cover, and only call a
   feature missing with the source path that shows it.
3. **Live.** Drive every feature at least once, through the project's verify skill (verify-unity for a Unity game),
   with as few separate setups as possible. Hold three rules the whole pass, whatever goes wrong:
   - run the doctor before the first drive and again after anything surprising, and reset to a known state rather than
     driving one you don't trust;
   - evidence you've captured survives every cleanup, checked where you said it is;
   - nothing you started outlives its use: play mode stopped, scenes reopened as they were, temporary files gone.
   A feature you can't reach is "unreachable" only with what it needs (an account, a device, a build, a state) and the
   route you tried; if the map doesn't say it needs that, that's drift too.
4. **Sort what you found.**
   - **Drift:** the map is wrong or missing something. Fix it, and drive the fixed steps once more.
   - **Harness gap:** it works, but the harness can't drive it. Fix the map's steps if you can; otherwise say what the
     tool would need.
   - **Regression:** the project actually doesn't do what it should. Report it with its proof (the steps and what
     happened). Don't fix it here: it's new work for the lead to hand out.
5. **Hand back.** Set "Last walked" to today and the commit you walked. `output`: the map files you changed (only
   those). `evidence`: the features driven and what each showed, a line each. `open`: regressions, unreachable
   features with what they need, and harness gaps. Start `done` with the outcome.
