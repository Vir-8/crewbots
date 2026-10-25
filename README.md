# crewbots

A crew of persistent coding agents that you manage instead of babysit. You give a goal to a coordinator bot. It splits the goal into tasks with one owner each and hands them to worker bots, which run in parallel, each in its own git worktree. A reviewer bot checks the work before it reaches you, and you hear back when there is something to approve.

The agents run on the official **Claude Code** and **Codex** CLIs in non-interactive mode, so they use the Claude and ChatGPT plans you already have. crewbots is the layer above them: bots, tasks, worktrees, delegation, rules and the app. The plan is in [docs/DESIGN.md](docs/DESIGN.md).

## Status

Milestone 1, the backends, is done:

- `ClaudeCLI` and `CodexCLI` run one prompt in a directory and translate each CLI's JSON output into one shared event stream (`crewbots.events`). They can also resume a session.
- The `read_only`, `workspace_write` and `full` access levels map onto each CLI's permission and sandbox flags.
- Bots only get crewbots' own MCP servers, never your personal ones. `ANTHROPIC_API_KEY` is hidden from Claude by default, so a run stays on your subscription.
- A run that times out or is stopped kills the CLI's whole process group.
- `crewbots.worktree` gives each task its own checkout and branch (`crewbots/<task>`) under `~/.crewbots/worktrees`.

## Try it

You need `claude` and `codex` installed and signed in.

```bash
uv sync
```

```bash
uv run crewbots run "What does this repo do? One paragraph." --backend claude
```

```bash
uv run crewbots run "Add a hello() function to hello.py" --backend codex --access workspace_write --worktree
```

`--worktree` runs the bot in a fresh worktree of the current repo and prints its diff, leaving your checkout untouched. `--json` prints the raw events.

## Tests

```bash
uv run pytest -q
```

No test calls a model. The backends run against `tests/fake_cli.py`, which replays real CLI output saved in `tests/fixtures/`.

## Layout

```
src/crewbots/
  events.py        the shared event stream
  backends/        claude_cli, codex_cli, and the subprocess runner they share
  worktree.py      one git worktree per task
  cli.py           `crewbots run`
tests/             one file per module, plus the fake CLI and fixtures
docs/DESIGN.md     where this is going
```
