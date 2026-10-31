What good work looks like. The reviewer checks against this file. A mistake that comes back becomes a check (a test, lint rule or CI step) where it can, and a rule here where it can't, saying who looks for it.

## Code

- The change does what the task asked, and nothing it didn't ask for.
- New behaviour has a test, and the project's existing tests still pass.
- Names and structure match the code around them.
- Work that changes what a user sees (a screen, a page, a rendered scene) carries a screenshot of it in the handback,
  and the reviewer's verdict says what the screenshot shows.
- A change to a .gitignore lists what it newly ignores in its handback, and the reviewer reads the list.
- No comments that excuse or defer: no workaround, hack or "for now" notes, no TODO or FIXME for what's unfinished
  (that goes in the handoff's `open`), no review feedback written into the code, no commented-out code. Fix the cause
  instead. Beyond that, comment style follows the project's AGENTS.md. crewbots refuses a handback whose new code adds
  an excuse comment; the reviewer looks for the rest.
- No secrets, credentials or personal data in code, logs or commits.

## Reports

- Lead with what needs a decision, then what was done, then where it is.
- Say what was not done or not verified. Never claim a check that was not run.

## Talking to the user

- Talk about the work, not how you do it. Don't tell the user which tools you have or lack, or how a teammate will go
  about something. What you can't check yourself, have a teammate check: say what you're doing about it ("Checking.")
  and come back with what you found, not with what you couldn't confirm.
- While a teammate looks into something, a line is enough. Its thread shows the rest, and the answer follows when
  it's back: what it is, what it means, and the choice if there is one.

## Engineering

Adapted from [pstack](https://github.com/cursor/plugins/tree/main/pstack)'s principles:

- **Prove it works.** Check the real thing: run the feature, read the actual output, inspect the diff. "It compiles" and "the tests I wrote pass" are not proof on their own.
- **Fix root causes.** Trace a symptom back to the first place things went wrong and fix it there. A guard is a fix only when the state it handles is legitimate.
- **Subtract before you add.** The best change is often the smallest one. Remove before you build around.
- **Don't block on the human.** Proceed, show the result, and let the user course-correct. Save questions for decisions that are theirs: irreversible, external, or out of scope.
- **Guard the context window.** Pass summaries and file paths between bots, not raw dumps.
