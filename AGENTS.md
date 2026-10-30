# Working on crewbots

A Python daemon (`src/crewbots`) and a Tauri app (`app/`, React and TypeScript). docs/ARCHITECTURE.md says how they fit.

## Checks

- `uv run ruff check src tests`
- `uv run pytest -q` (the whole suite takes about 3 minutes: while you work, run the tests for what you changed, and
  the whole suite once before handing back)
- `cd app && npx tsc --noEmit -p . && npm test`
- After changing the API: `uv run crewbots openapi --ts --out app/src/api-types.ts`

## Prove it works

Follow `.agents/verify/README.md`, which also maps each feature, for any change to `app/` or `src/`. Run
the change in the design preview, WebKit or a scratch daemon, and hand back the evidence. "Tests pass" alone isn't done.
`scripts/verify` starts and stops what you need, in the background. Never run a dev server in the foreground.

## How code is written here

- Write like the code around it: small plain names, and comments only for a "why" that isn't obvious.
- Keep files within the limits in `tests/test_file_sizes.py`: split a file by area rather than raising its limit.
- Add a dependency only when it's fast and clearly worth it.
- Commit messages are plain and descriptive, with no labels like "phase 1" and no AI co-author trailers.
- Code review (`src/crewbots/reviews.py` and its pages) is frozen: change it only when asked.
