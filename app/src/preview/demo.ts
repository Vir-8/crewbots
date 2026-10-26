// A made-up crew for designing the UI in a plain browser (`npm run vite`, then open localhost:5173). Every state the
// app has to show is here: work in progress, a question for you, approved work waiting to merge, a failure, and
// finished, merged work. It's shaped exactly like the daemon's data, so the UI can't tell the difference.

import type { Bot, ReviewState, StoredEvent, Task } from "../types";

export const bots: Bot[] = [
  { name: "chief", title: "Chief of Staff", description: "Plans goals into tasks, tracks them, and reports back.", backend: "claude", model: "opus", access: "read_only", workspace: null, network: false, mcp: [], skills: ["interrogate", "arena", "swarm"], can_delegate: true, path: "~/.crewbots/crew/bots/chief.md", display_name: null, avatar: null, effort: null, context_window: null },
  { name: "engineer", title: "Engineer", description: "Writes and changes code, with tests, on its own branch.", backend: "codex", model: null, access: "workspace_write", workspace: null, network: true, mcp: [], skills: [], can_delegate: false, path: "~/.crewbots/crew/bots/engineer.md", display_name: null, avatar: null, effort: null, context_window: 200000 },
  { name: "reviewer", title: "Reviewer", description: "Checks work against team/standards.md.", backend: "claude", model: "sonnet", access: "read_only", workspace: null, network: false, mcp: [], skills: [], can_delegate: false, path: "~/.crewbots/crew/bots/reviewer.md", display_name: null, avatar: null, effort: null, context_window: 200000 },
  { name: "critic", title: "Critic", description: "A second reviewer on another model family; tries to break the work.", backend: "codex", model: null, access: "read_only", workspace: null, network: false, mcp: [], skills: [], can_delegate: false, path: "~/.crewbots/crew/bots/critic.md", display_name: null, avatar: null, effort: null, context_window: 200000 },
];

let clock = Date.now() - 1000 * 60 * 60 * 5;
const tick = (minutes = 0.4) => new Date((clock += minutes * 60_000)).toISOString();
let nextEvent = 1;
export const tasks = new Map<string, Task>();
export const events = new Map<string, StoredEvent[]>();
export const reviews = new Map<string, ReviewState>();
export const diffs = new Map<string, string>();

function task(id: string, bot: string, title: string, fields: Partial<Task> = {}): Task {
  const at = tick(0.2);
  const t: Task = {
    id, bot, title, status: "idle", created_by: "user", parent_id: null, workspace: "~/code/acme-web", repo: null,
    worktree_path: null, branch: null, base: null, session_id: `s-${id}`, result: null, error: null,
    input_tokens: 40_000, output_tokens: 1_200, cost_usd: 0, reported: true, turns: 1, review_of: null,
    review_version: null, merge_state: null, merged_commit: null, kind: "thread", room_id: null, pr: null, routine: null, budget: null, created_at: at,
    updated_at: at, ...fields,
  };
  tasks.set(id, t);
  events.set(id, []);
  return t;
}

function work(id: string, bot: string, title: string, parent: string, fields: Partial<Task> = {}): Task {
  return task(id, bot, title, {
    created_by: "chief", parent_id: parent, repo: "~/code/acme-web",
    worktree_path: `~/.crewbots/worktrees/acme-web/${id}`, branch: `crewbots/${id}`, base: "a1b2c3d", ...fields,
  });
}

export function emit(taskId: string, kind: string, data: Record<string, unknown>): StoredEvent {
  const event: StoredEvent = { id: nextEvent++, task_id: taskId, kind, data, at: tick() };
  events.get(taskId)!.push(event);
  const t = tasks.get(taskId)!;
  tasks.set(taskId, { ...t, updated_at: event.at });
  return event;
}

const say = (id: string, text: string) => emit(id, "text", { text });
const message = (id: string, from: string, text: string) => emit(id, "message", { from, text });
let callId = 0;
function tool(id: string, name: string, input: Record<string, unknown>, output = "", isError = false) {
  const call = `c${callId++}`;
  emit(id, "tool_call", { id: call, name, input });
  emit(id, "tool_result", { id: call, output, is_error: isError });
}
const done = (id: string, text = "") => emit(id, "done", { final_text: text, is_error: false, error: null });
const usage = (id: string) => emit(id, "usage", { input_tokens: 38_000, output_tokens: 900, cached_input_tokens: 31_000, cost_usd: null });

function verdict(reviewTask: string, work: string, reviewer: string, version: number, pass: boolean, summary: string, findings: object[] = []) {
  const data = { id: nextEvent, task_id: reviewTask, work_id: work, reviewer, version, verdict: pass ? "pass" : "fail", summary, findings, at: tick() };
  emit(reviewTask, "review", data);
  emit(work, "review", data);
}

// 1. Merged earlier today: truncate().
{
  const g = task("t_truncate01", "chief", "Add truncate(s, n) to text.py with tests, reviewed before it lands", { merge_state: null, updated_at: tick(), input_tokens: 100_000 });
  message(g.id, "user", "Add truncate(s, n) to text.py: shorten s to at most n characters, ending with an ellipsis when it cuts. Decide the edge cases yourself. Tests required, and review it before it reaches me.");
  say(g.id, "I'll read the module and its tests first.");
  tool(g.id, "Read", { file_path: "text.py" }, "def shout(s): ...");
  tool(g.id, "Read", { file_path: "test_text.py" }, "def test_shout(): ...");
  say(g.id, "Small, well-contained change. I'm giving it to the engineer, with negative `n` returning `\"\"` and `n < 3` returning dots only, then running a two-model review.");
  const e = work("t_truncate02", "engineer", "Add truncate(s, n) to text.py, with tests for every edge case", g.id, { merge_state: "merged", merged_commit: "6db169d0d6ef", turns: 1, result: "Added `truncate(s, n)` and 11 tests. `python -m pytest` passes (11 tests)." });
  tool(g.id, "mcp__crewbots__delegate", { bot: "engineer", goal: e.title }, `Delegated ${e.id} to engineer`);
  done(g.id);
  message(e.id, "chief", e.title);
  emit(e.id, "worktree", { path: e.worktree_path, branch: e.branch, base: "a1b2c3d" });
  tool(e.id, "shell", { command: "sed -n '1,80p' text.py test_text.py" });
  tool(e.id, "apply_patch", { changes: [{ path: "text.py", kind: "update" }, { path: "test_text.py", kind: "update" }] }, "completed");
  tool(e.id, "shell", { command: "python -m pytest -q" }, "11 passed in 0.02s");
  emit(e.id, "handoff", { done: "Added truncate(s, n) with 11 tests; all pass.", output: ["text.py", "test_text.py"], open: "", needs: "Review" });
  say(e.id, e.result!);
  usage(e.id);
  done(e.id, e.result!);
  const r = work("t_truncate03", "reviewer", "Review truncate(s, n)", g.id, { review_of: e.id, review_version: 1, worktree_path: null, branch: null });
  const c = work("t_truncate04", "critic", "Try to break truncate(s, n)", g.id, { review_of: e.id, review_version: 1, worktree_path: null, branch: null });
  message(g.id, "crewbots", `1 thread(s) you opened or delegated finished, and none are still running.\n\n## ${e.id} · engineer · idle`);
  say(g.id, "The engineer is done and the tests pass. Sending it to the reviewer and the critic.");
  tool(g.id, "mcp__crewbots__delegate", { bot: "reviewer", goal: r.title, review_of: e.id }, `Delegated ${r.id}`);
  tool(g.id, "mcp__crewbots__delegate", { bot: "critic", goal: c.title, review_of: e.id }, `Delegated ${c.id}`);
  done(g.id);
  message(r.id, "chief", r.title);
  tool(r.id, "Read", { file_path: "text.py" });
  verdict(r.id, e.id, "reviewer", 1, true, "Correct for every edge case, and every case has a test.");
  done(r.id);
  message(c.id, "chief", c.title);
  tool(c.id, "shell", { command: "python - <<'EOF' # 610 boundary checks" }, "610 passed");
  verdict(c.id, e.id, "critic", 1, true, "No provable defect. 610 boundary and property checks passed, including Unicode and huge n.");
  done(c.id);
  message(g.id, "crewbots", "2 thread(s) you opened or delegated finished, and none are still running.\n\n# Review status\n\nround 1 of 3: approved");
  say(g.id, "Both reviewers passed it, so I've proposed it for merging.\n\n- **What changed:** `truncate(s, n)`, plus 11 tests\n- **Edge cases:** negative `n` gives `\"\"`, and `n < 3` gives dots only\n- **Worth considering:** it counts code points, not graphemes");
  tool(g.id, "mcp__crewbots__propose_merge", { task_id: e.id, summary: "truncate(s, n) with 11 tests" }, `Proposed ${e.id}`);
  emit(e.id, "merge_proposed", { by: "chief", summary: "truncate(s, n) with 11 tests; two reviewers passed it." });
  emit(e.id, "merged", { commit: "6db169d0d6ef", into: "~/code/acme-web", forced: false });
  done(g.id);
  reviews.set(e.id, { work_id: e.id, version: 1, round: 1, max_rounds: 3, approved: true, failed: false, out_of_rounds: false, reviews: [
    { task_id: r.id, reviewer: "reviewer", status: "idle", verdict: "pass", summary: "Correct for every edge case, and every case has a test.", findings: [] },
    { task_id: c.id, reviewer: "critic", status: "idle", verdict: "pass", summary: "No provable defect. 610 boundary checks passed.", findings: [] },
  ] });
  diffs.set(e.id, "diff --git a/text.py b/text.py\n--- a/text.py\n+++ b/text.py\n@@ -1,2 +1,12 @@\n def shout(s):\n     return s.upper() + \"!\"\n+\n+\n+def truncate(s, n):\n+    if n < 0:\n+        return \"\"\n+    if len(s) <= n:\n+        return s\n+    if n < 3:\n+        return \".\" * n\n+    return s[: n - 3] + \"...\"\n");
}

// 2. Held: the plan's five-hour limit was reached, so its next turn waits for the reset.
export const resetsAt = Math.floor(Date.now() / 1000) + 2 * 3600;
{
  const g = task("t_docs000001", "chief", "Translate the onboarding docs into Spanish", { status: "held" });
  message(g.id, "user", "Translate the onboarding docs in docs/onboarding/ into Spanish, keeping the formatting.");
  say(g.id, "There are 14 files. I'll split them across two tasks.");
  usage(g.id);
  done(g.id, "There are 14 files. I'll split them across two tasks.");
  emit(g.id, "rate_limit", { plan: "claude", window: "five_hour", status: "limited", used_percent: 100, resets_at: resetsAt });
  message(g.id, "user", "Go ahead.");
  const resets = new Date(resetsAt * 1000).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false });
  emit(g.id, "held", { reason: `the Claude plan's five-hour limit is reached; it resets ${resets}.` });
}

// A routine: this morning's standup.
{
  const g = task("t_stand00001", "chief", "Standup, Mon 26 Oct", { created_by: "routine", routine: "standup", budget: 200_000, workspace: null });
  message(g.id, "routine", "Write the standup for what the crew did since Fri 23 Oct 09:00, from the record below.");
  say(g.id, "**Needs you**\n- **Upgrade to React 19**: replace legacy-charts with recharts, or stay on React 18?\n- **Fix the flaky login test** is reviewed and ready to merge.\n\n**Done**\n- `truncate` helper: passed review in round 2.\n\n**In progress**\n- CSV export: the engineer is on the endpoint and the button.");
  usage(g.id);
  done(g.id);
}

// 3. Waiting on you: approved and proposed for merging, after a failed first round.
{
  const g = task("t_login00001", "chief", "Fix the flaky login test", { status: "idle" });
  message(g.id, "user", "The login test fails about one run in ten on CI. Find out why and fix it properly, not with a retry.");
  say(g.id, "I'll look at the test and how CI runs it.");
  tool(g.id, "Grep", { pattern: "test_login" }, "tests/test_auth.py:41");
  tool(g.id, "Read", { file_path: "tests/test_auth.py" });
  say(g.id, "It reads the session cookie before the redirect finishes, so it races the server. That's a real bug in the test, not a flaky server. Giving it to the engineer.");
  const e = work("t_login00002", "engineer", "Make test_login wait for the redirect instead of racing it", g.id, { turns: 2, merge_state: "proposed", result: "The test now waits for the redirect response. 500 runs in a row pass, where it failed 48 of 500 before." });
  tool(g.id, "mcp__crewbots__delegate", { bot: "engineer", goal: e.title }, `Delegated ${e.id}`);
  done(g.id);
  message(e.id, "chief", e.title);
  emit(e.id, "worktree", { path: e.worktree_path, branch: e.branch, base: "a1b2c3d" });
  tool(e.id, "shell", { command: "for i in $(seq 500); do pytest -q tests/test_auth.py::test_login || echo FAIL; done | grep -c FAIL" }, "48");
  tool(e.id, "apply_patch", { changes: [{ path: "tests/test_auth.py", kind: "update" }] }, "completed");
  say(e.id, "Added a sleep before reading the cookie; 500 runs pass.");
  done(e.id);
  const r1 = work("t_login00003", "reviewer", "Review the login test fix", g.id, { review_of: e.id, review_version: 1, worktree_path: null, branch: null });
  tool(g.id, "mcp__crewbots__delegate", { bot: "reviewer", goal: r1.title, review_of: e.id }, `Delegated ${r1.id}`);
  verdict(r1.id, e.id, "reviewer", 1, false, "A sleep hides the race instead of removing it.", [{ location: "tests/test_auth.py:47", problem: "time.sleep(0.5) before reading the cookie", scenario: "on a slow CI runner the redirect takes longer than 0.5s and the test fails again" }]);
  done(r1.id);
  say(g.id, "The reviewer is right: a sleep is a retry in disguise. Sending it back.");
  tool(g.id, "mcp__crewbots__message", { task_id: e.id, text: "Wait for the redirect response itself, not a fixed time." }, "Sent");
  message(e.id, "chief", "Wait for the redirect response itself, not a fixed time.");
  tool(e.id, "apply_patch", { changes: [{ path: "tests/test_auth.py", kind: "update" }] }, "completed");
  tool(e.id, "shell", { command: "pytest -q tests/test_auth.py --count 500" }, "500 passed in 41.2s");
  emit(e.id, "handoff", { done: "test_login waits for the redirect response; 500 of 500 runs pass.", output: ["tests/test_auth.py"], open: "", needs: "Review round 2" });
  say(e.id, e.result!);
  done(e.id);
  const r2 = work("t_login00004", "reviewer", "Review the login test fix again", g.id, { review_of: e.id, review_version: 2, worktree_path: null, branch: null });
  const c2 = work("t_login00005", "critic", "Try to break the login test fix", g.id, { review_of: e.id, review_version: 2, worktree_path: null, branch: null });
  verdict(r2.id, e.id, "reviewer", 2, true, "Waits on the actual response now. The race is gone.");
  done(r2.id);
  verdict(c2.id, e.id, "critic", 2, true, "Couldn't make it fail in 2,000 runs, including with an artificial 3s redirect delay.");
  done(c2.id);
  say(g.id, "Round 2 passed with both reviewers, so it's ready for you to merge. The fix waits for the redirect itself, and 500 of 500 runs pass, against 48 failures in 500 before.");
  tool(g.id, "mcp__crewbots__propose_merge", { task_id: e.id, summary: "Wait for the redirect instead of racing it" }, `Proposed ${e.id}`);
  emit(e.id, "merge_proposed", { by: "chief", summary: "The login test waits for the redirect response instead of racing it. 500/500 runs pass." });
  done(g.id);
  reviews.set(e.id, { work_id: e.id, version: 2, round: 2, max_rounds: 3, approved: true, failed: false, out_of_rounds: false, reviews: [
    { task_id: r2.id, reviewer: "reviewer", status: "idle", verdict: "pass", summary: "Waits on the actual response now. The race is gone.", findings: [] },
    { task_id: c2.id, reviewer: "critic", status: "idle", verdict: "pass", summary: "Couldn't make it fail in 2,000 runs.", findings: [] },
  ] });
  diffs.set(e.id, "diff --git a/tests/test_auth.py b/tests/test_auth.py\n--- a/tests/test_auth.py\n+++ b/tests/test_auth.py\n@@ -44,7 +44,8 @@ def test_login(client):\n     response = client.post(\"/login\", data=CREDENTIALS)\n-    cookie = client.cookies.get(\"session\")\n+    redirect = client.wait_for_redirect(response, timeout=10)\n+    cookie = redirect.cookies.get(\"session\")\n     assert cookie\n");
}

// 4. Waiting on you: a question.
{
  const g = task("t_react00001", "chief", "Upgrade the app to React 19", { status: "needs_input" });
  message(g.id, "user", "Upgrade the app to React 19 and fix whatever breaks.");
  say(g.id, "I'll check what depends on React's internals first.");
  tool(g.id, "Grep", { pattern: "react-dom/server|unstable_" }, "src/charts/Legacy.tsx:3");
  tool(g.id, "Read", { file_path: "package.json" });
  say(g.id, "The upgrade itself is mechanical, but `legacy-charts` uses APIs React 19 removed, and its last release was in 2023.");
  emit(g.id, "escalation", { question: "legacy-charts doesn't work with React 19 and is unmaintained. Should I replace it with recharts (about 6 chart components to port), or keep React 18 for now?" });
  say(g.id, "I need your call before going further: replace `legacy-charts` with recharts, or stay on React 18 for now?");
  done(g.id);
}

// 5. Working right now.
{
  const g = task("t_csv0000001", "chief", "Add CSV export to the reports page", { status: "idle" });
  message(g.id, "user", "Add a CSV export button to the reports page. Exports should respect the current filters.");
  say(g.id, "I'll find where the report data and the filters come from.");
  tool(g.id, "Grep", { pattern: "useReportFilters" }, "src/reports/Reports.tsx:12");
  tool(g.id, "Read", { file_path: "src/reports/Reports.tsx" });
  tool(g.id, "Read", { file_path: "src/api/reports.ts" });
  say(g.id, "The filters already reach the API, so the export can reuse the same query on the server. I've given it to the engineer: an `/reports/export.csv` endpoint plus the button, with tests for the filters.");
  const e = work("t_csv0000002", "engineer", "Add /reports/export.csv and an Export button that uses the current filters", g.id, { status: "running", reported: false, turns: 0 });
  tool(g.id, "mcp__crewbots__delegate", { bot: "engineer", goal: e.title }, `Delegated ${e.id}`);
  done(g.id);
  message(e.id, "chief", e.title);
  emit(e.id, "worktree", { path: e.worktree_path, branch: e.branch, base: "a1b2c3d" });
  say(e.id, "I'll add the endpoint next to the existing report query, then the button.");
  tool(e.id, "shell", { command: "rg -n 'def report_rows' server/" }, "server/reports.py:58");
  tool(e.id, "apply_patch", { changes: [{ path: "server/reports.py", kind: "update" }] }, "completed");
  emit(e.id, "tool_call", { id: "c-live", name: "shell", input: { command: "pytest -q server/tests/test_reports.py" } });
  diffs.set(e.id, "diff --git a/server/reports.py b/server/reports.py\n--- a/server/reports.py\n+++ b/server/reports.py\n@@ -58,6 +58,18 @@ def report_rows(filters):\n     return query.all()\n+\n+\n+@router.get(\"/reports/export.csv\")\n+def export_csv(filters: Filters = Depends()):\n+    rows = report_rows(filters)\n+    return csv_response(rows, filename=\"report.csv\")\n");
}

export const live = { taskId: "t_csv0000002" };

// A private review of acme/web#412: two reviewers on their own, then the lead's one review and its drafted comment.
export const pullReviews: Task[] = [];
{
  const base = { kind: "review" as const, pr: "acme/web#412", workspace: "~/.crewbots/reviews/checkouts/acme-web-412-abc", title: "Review acme/web#412: Move the export button into the reports toolbar" };
  const lead = task("t_pr412lead", "reviewer:claude:sonnet:high", base.title, base);
  const kids = [task("t_pr412son", "reviewer:claude:sonnet:high", base.title, { ...base, parent_id: lead.id }), task("t_pr412gpt", "reviewer:codex:default:high", base.title, { ...base, parent_id: lead.id })];
  message(lead.id, "user", "Review https://github.com/acme/web/pull/412");
  emit(lead.id, "notice", { message: "Getting acme/web's code. The first review of a repository takes about a minute; later ones, a few seconds." });
  for (const k of kids) {
    message(k.id, "user", "Review https://github.com/acme/web/pull/412.\n\nYour working directory is the repository, checked out at the pull request's head (abc123def456).");
    tool(k.id, "mcp__crewbots__pr_overview", {}, "# Move the export button into the reports toolbar");
    tool(k.id, "mcp__crewbots__pr_diff", {}, "Part 1 of 1.");
    tool(k.id, k.id === "t_pr412son" ? "Grep" : "shell", k.id === "t_pr412son" ? { pattern: "export_csv" } : { command: "rg -n 'filename=' server/" }, "server/reports.py:64");
    say(k.id, "**P1** `server/reports.py:64`: `filename` is fixed, so two exports in a row overwrite each other in the browser's downloads.\n\nOverall risk: low. I didn't run anything.");
    done(k.id);
  }
  message(lead.id, "crewbots", "The reviewers have finished.");
  tool(lead.id, "mcp__crewbots__pr_diff", { path: "server/reports.py" }, "diff --git a/server/reports.py b/server/reports.py");
  tool(lead.id, "Read", { file_path: "server/reports.py" });
  tool(lead.id, "mcp__crewbots__draft_comment", { path: "server/reports.py", line: 64, body: "…" }, "Drafted comment 1 on server/reports.py:64.");
  say(lead.id, "One issue, not blocking. I drafted a comment for it.\n\n**Findings**\n\n1. **P1** `server/reports.py:64`\n   - The export's filename is fixed, so a second export overwrites the first in the browser's downloads.\n   - Both reviewers found it.\n\n2. **P2** `web/src/reports/Toolbar.tsx:40` (no comment drafted)\n   - The button has no busy state, so a double click starts two exports.\n   - Sonnet found this; GPT didn't.\n\n**Overall impact:** small. The button moves; the endpoint's behaviour is unchanged.\n\n**Couldn't check:** how Safari names repeated downloads. Nothing was run.");
  done(lead.id);
  pullReviews.push(tasks.get(lead.id)!, ...kids.map((k) => tasks.get(k.id)!));
}

// Chats: one ongoing chat with each bot. The chief's opened the work above as threads, so they hang from it.
export const chatIds: Record<string, string> = {};
for (const b of bots) {
  chatIds[b.name] = task(`t_chat_${b.name}`.slice(0, 12).padEnd(12, "0"), b.name, `Chat with ${b.name}`, { kind: "chat", workspace: null }).id;
}
{
  const chief = chatIds.chief;
  const opened = ["t_truncate01", "t_docs000001", "t_login00001", "t_react00001", "t_csv0000001"].filter((id) => tasks.has(id));
  for (const id of opened) tasks.set(id, { ...tasks.get(id)!, parent_id: chief });
  message(chief, "user", "Add truncate(s, n) to text.py, with tests, and have it reviewed before it lands.");
  tool(chief, "mcp__crewbots__start_thread", { title: "Add truncate(s, n)", goal: "…" }, "Opened thread");
  say(chief, "On it. I've opened a thread for it; the engineer writes it and two reviewers check it.");
  message(chief, "user", "Also: translate the onboarding docs into Spanish, fix the flaky login test, and look at upgrading to React 19.");
  for (const id of opened.slice(1, 4)) tool(chief, "mcp__crewbots__start_thread", { title: tasks.get(id)!.title, goal: "…" }, "Opened thread");
  say(chief, "Three threads, one each. React 19 will need a call from you on the charts library.");
  message(chief, "routine:standup", "[routine: standup]\n\nWrite the standup for what the crew did since Fri 23 Oct 09:00.");
  say(chief, "**Morning. Two things need you**\n- **React 19**: replace legacy-charts with recharts, or stay on React 18?\n- **Flaky login test**: reviewed and ready to merge.\n\n**Done**: `truncate`, merged after review.\n\n**Waiting**: the Spanish docs, until the Claude plan's limit resets.");
  message(chief, "user", "Merge the login fix. Then add CSV export to the reports page; exports should respect the filters.");
  tool(chief, "mcp__crewbots__start_thread", { title: "Add CSV export", goal: "…" }, "Opened thread");
  say(chief, "Merged. The CSV export is with the engineer now, and the reviewer and critic will check it before it comes to you.");
  done(chief);

  message(chatIds.engineer, "user", "Can you keep the test suite under 30 seconds?");
  say(chatIds.engineer, "It's at 18s now. The slowest file is `test_reports.py` (6s), mostly fixtures; I can trim that if it grows.");
  done(chatIds.engineer);
  message(chatIds.reviewer, "user", "What do you look for first?");
  say(chatIds.reviewer, "That the change does what the task asked, with a test for each edge case. Then `team/standards.md`.");
  done(chatIds.reviewer);
}

// A group chat: the crew's own, where bots talk to each other in the open.
// And a new one nobody has written in yet.
export const rooms = [
  { id: "r_crew000001", title: "Crew", lead: "chief", members: bots.map((b) => b.name), created_at: tick(), updated_at: tick() },
  { id: "r_review0002", title: "Review desk", lead: "reviewer", members: ["reviewer", "critic"], created_at: tick(), updated_at: tick() },
];
export const roomMessages = new Map<string, { id: number; room_id: string; author: string; text: string; depth: number; at: string }[]>();
{
  let id = 1;
  const room = rooms[0].id;
  const said = (author: string, text: string, depth = 0) => ({ id: id++, room_id: room, author, text, depth, at: tick(1) });
  roomMessages.set(room, [
    said("user", "Morning, everyone. @engineer how's CSV export going?"),
    said("engineer", "Endpoint's in and its tests pass. Wiring the button now. @reviewer it'll be yours in a few minutes.", 1),
    said("reviewer", "Ready when you are. I'll check the filters reach the export.", 2),
    said("chief", "Thanks both. I'll propose it for merging once both reviews pass.", 1),
  ]);
}
