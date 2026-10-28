// A made-up crew for designing the UI in a plain browser (`npm run vite`, then open localhost:5173). Every state the
// app has to show is here: work in progress, a question for you, approved work waiting to merge, a failure, and
// finished, merged work. It's shaped exactly like the daemon's data, so the UI can't tell the difference.

import type { Bot, ReviewState, StoredEvent, Task } from "../../types";

export const bots: Bot[] = [
  { name: "chief", title: "Chief of Staff", description: "Plans goals into tasks, tracks them, and reports back.", backend: "claude", model: "opus", access: "read_only", workspace: null, network: false, mcp: [], skills: ["interrogate", "arena", "swarm"], can_delegate: true, path: "~/.crewbots/crew/bots/chief.md", display_name: "Roger", avatar: null, effort: null, context_window: null },
  { name: "engineer", title: "Engineer", description: "Writes and changes code, with tests, on its own branch.", backend: "codex", model: null, access: "workspace_write", workspace: null, network: true, mcp: [], skills: [], can_delegate: false, path: "~/.crewbots/crew/bots/engineer.md", display_name: null, avatar: null, effort: null, context_window: 200000 },
  { name: "reviewer", title: "Reviewer", description: "Checks work against team/standards.md.", backend: "claude", model: "sonnet", access: "read_only", workspace: null, network: false, mcp: [], skills: [], can_delegate: false, path: "~/.crewbots/crew/bots/reviewer.md", display_name: null, avatar: null, effort: null, context_window: 200000 },
  { name: "critic", title: "Critic", description: "A second reviewer on another model family; tries to break the work.", backend: "codex", model: null, access: "read_only", workspace: null, network: false, mcp: [], skills: [], can_delegate: false, path: "~/.crewbots/crew/bots/critic.md", display_name: null, avatar: null, effort: null, context_window: 200000 },
];

let clock = Date.now() - 1000 * 60 * 60 * 5;
export const tick = (minutes = 0.4) => new Date((clock += minutes * 60_000)).toISOString();
let nextEvent = 1;
export const tasks = new Map<string, Task>();
export const events = new Map<string, StoredEvent[]>();
export const reviews = new Map<string, ReviewState>();
export const diffs = new Map<string, string>();

export function task(id: string, bot: string, title: string, fields: Partial<Task> = {}): Task {
  const at = tick(0.2);
  const t: Task = {
    id, bot, title, status: "idle", created_by: "user", parent_id: null, workspace: "~/code/acme-web", repo: null,
    worktree_path: null, branch: null, base: null, session_id: `s-${id}`, result: null, error: null,
    input_tokens: 40_000, output_tokens: 1_200, cost_usd: 0, reported: true, turns: 1, review_of: null,
    review_version: null, merge_state: null, merged_commit: null, merged_tip: null, kind: "thread", room_id: null, pr: null, routine: null, budget: null, created_at: at,
    updated_at: at, ...fields,
  };
  tasks.set(id, t);
  events.set(id, []);
  return t;
}

export function work(id: string, bot: string, title: string, parent: string, fields: Partial<Task> = {}): Task {
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

export const say = (id: string, text: string) => emit(id, "text", { text });
export const message = (id: string, from: string, text: string) => emit(id, "message", { from, text });
let callId = 0;
export function tool(id: string, name: string, input: Record<string, unknown>, output = "", isError = false) {
  const call = `c${callId++}`;
  emit(id, "tool_call", { id: call, name, input });
  emit(id, "tool_result", { id: call, output, is_error: isError });
}
export const done = (id: string, text = "") => emit(id, "done", { final_text: text, is_error: false, error: null });
export const usage = (id: string) => emit(id, "usage", { input_tokens: 38_000, output_tokens: 900, cached_input_tokens: 31_000, cost_usd: null });

export function verdict(reviewTask: string, work: string, reviewer: string, version: number, pass: boolean, summary: string, findings: object[] = []) {
  const data = { id: nextEvent, task_id: reviewTask, work_id: work, reviewer, version, verdict: pass ? "pass" : "fail", summary, findings, at: tick() };
  emit(reviewTask, "review", data);
  emit(work, "review", data);
}
