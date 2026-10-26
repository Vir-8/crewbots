// The bridge the UI uses outside Tauri: it answers the API from the demo crew, and plays a little live activity, so
// the UI can be designed and checked in an ordinary browser.

import type { ConnectionStatus, Live, StoredEvent, Task } from "../types";
import * as demo from "./demo";

type Listener<T> = (payload: T) => void;
const liveListeners = new Set<Listener<Live>>();
const publish = (message: Live) => liveListeners.forEach((l) => l(message));
const publishTask = (task: Task) => publish({ type: "task", task });
const publishEvent = (event: StoredEvent) => publish({ type: "event", event });

function update(id: string, fields: Partial<Task>) {
  const task = { ...demo.tasks.get(id)!, ...fields, updated_at: new Date().toISOString() };
  demo.tasks.set(id, task);
  publishTask(task);
  return task;
}

function say(id: string, kind: string, data: Record<string, unknown>) {
  publishEvent(demo.emit(id, kind, data));
}

// Keep the working engineer visibly busy.
const steps = [
  { name: "shell", input: { command: "pytest -q server/tests/test_reports.py" } },
  { name: "apply_patch", input: { changes: [{ path: "web/src/reports/ExportButton.tsx", kind: "add" }] } },
  { name: "shell", input: { command: "npm test -- ExportButton" } },
  { name: "Read", input: { file_path: "web/src/reports/Reports.tsx" } },
];
let step = 0;
setInterval(() => {
  const s = steps[step++ % steps.length];
  say(demo.live.taskId, "tool_call", { id: `live-${step}`, name: s.name, input: s.input });
}, 3500);

const ok = (data: unknown) => ({ ok: true, status: 200, data });
const missing = (what: string) => ({ ok: false, status: 404, data: { detail: `no ${what}` } });

async function request(method: string, path: string, body?: any) {
  const url = new URL(path, "http://preview");
  const parts = url.pathname.split("/").filter(Boolean);
  const all = [...demo.tasks.values()];
  const allEvents = [...demo.events.values()].flat().sort((a, b) => a.id - b.id);
  await new Promise((r) => setTimeout(r, 60));

  if (method === "GET" && url.pathname === "/bots") return ok(demo.bots);
  if (method === "GET" && url.pathname === "/tasks") return ok(all.sort((a, b) => b.created_at.localeCompare(a.created_at)));
  if (method === "GET" && url.pathname === "/events/recent") return ok(allEvents.slice(-Number(url.searchParams.get("limit") ?? 400)));
  if (method === "GET" && url.pathname === "/events/cursor") return ok({ id: allEvents.at(-1)?.id ?? 0 });
  if (parts[0] === "bot") return { ok: false, status: 0, data: { detail: "not an app path" } };
  if (method === "GET" && url.pathname === "/crew") return ok(crewState);
  if (method === "GET" && url.pathname === "/routines") return ok(routines());
  if (method === "POST" && parts[0] === "routines" && parts[2] === "run") {
    return request("POST", "/tasks", { bot: "chief", goal: `Write the ${parts[1]} (a routine run by hand).` });
  }

  if (method === "POST" && url.pathname === "/tasks") {
    const id = `t_${Math.random().toString(16).slice(2, 12)}`;
    const at = new Date().toISOString();
    const task: Task = { ...demo.tasks.get("t_docs000001")!, id, bot: body.bot, title: body.goal.split("\n")[0].slice(0, 80), status: "running", error: null, created_at: at, updated_at: at, parent_id: null, workspace: body.workspace };
    demo.tasks.set(id, task);
    demo.events.set(id, []);
    publishTask(task);
    say(id, "message", { from: "user", text: body.goal });
    setTimeout(() => say(id, "text", { text: "I'll take a look at the code first." }), 900);
    setTimeout(() => say(id, "tool_call", { id: "p1", name: "Grep", input: { pattern: "TODO" } }), 1600);
    setTimeout(() => {
      say(id, "text", { text: "This is the design preview, so nothing really runs. In the app, the crew would be working on it now." });
      update(id, { status: "idle" });
    }, 2600);
    return ok(task);
  }

  const [, id, action] = parts;
  const task = id ? demo.tasks.get(id) : undefined;
  if (parts[0] !== "tasks" || !task) return missing(`task ${id}`);
  if (method === "GET" && !action) return ok(task);
  if (method === "GET" && action === "events") {
    const after = Number(url.searchParams.get("after") ?? 0);
    return ok((demo.events.get(id) ?? []).filter((e) => e.id > after));
  }
  if (method === "GET" && action === "review") {
    return ok(demo.reviews.get(id) ?? { work_id: id, version: task.turns, round: 0, max_rounds: 3, approved: false, failed: false, out_of_rounds: false, reviews: [] });
  }
  if (method === "GET" && action === "changes") return ok({ diff: task.worktree_path ? demo.diffs.get(id) ?? "" : null });
  if (method === "GET" && action === "children") return ok(all.filter((t) => t.parent_id === id));
  if (method === "POST" && action === "messages") {
    say(id, "message", { from: "user", text: body.text });
    update(id, { status: "running" });
    setTimeout(() => {
      say(id, "text", { text: "Got it. (This is the design preview; in the app the bot would act on that.)" });
      update(id, { status: "idle" });
    }, 1500);
    return ok(task);
  }
  if (method === "POST" && action === "cancel") return ok(update(id, { status: "cancelled" }));
  if (method === "POST" && action === "release") {
    say(id, "released", { goal: id });
    update(id, { status: "running" });
    setTimeout(() => update(id, { status: "idle" }), 1500);
    return ok(task);
  }
  if (method === "POST" && action === "merge") {
    say(id, "merged", { commit: "9f8e7d6c5b4a", into: task.repo ?? "~/code", forced: Boolean(body?.force) });
    return ok(update(id, { merge_state: "merged", merged_commit: "9f8e7d6c5b4a" }));
  }
  return missing(`${method} ${path}`);
}

const status: ConnectionStatus = { state: "connected", home: "design preview", ownsDaemon: false };

const crewState = {
  paused: false, running: 1, held: 1, sessions: 2,
  limits: [{ plan: "claude", window: "five_hour", status: "limited", used_percent: 100, resets_at: demo.resetsAt }],
};

function routines() {
  const at = (days: number, hour: number) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    d.setHours(hour, 0, 0, 0);
    return d.toISOString();
  };
  const base = { workspace: null, repo: null, on: null, budget: null, enabled: true, last_run_at: null, last_task_id: null, skipped_at: null };
  return {
    routines: [
      { ...base, name: "standup", description: "A short summary each weekday morning of what the crew did, and what needs you.", bot: "chief", when: "0 9 * * 1-5", schedule: "0 9 * * 1-5", path: "routines/standup.md", next_run_at: at(1, 9), last_run_at: at(0, 9), last_task_id: "t_stand00001", budget: 200_000 },
      { ...base, name: "retro", description: "A Friday look back at the week.", bot: "chief", when: "0 16 * * 5", schedule: "0 16 * * 5", path: "routines/retro.md", next_run_at: at(4, 16) },
      { ...base, name: "review-requests", description: "Drafts a review when someone asks you to review a pull request.", bot: "reviewer", when: "on github.review_requested in acme/web", schedule: null, on: "github.review_requested", repo: "acme/web", path: "routines/review-requests.md", next_run_at: null },
    ],
    errors: {},
    github_error: null,
  };
}

export const previewBridge = {
  status: async () => status,
  request,
  pickFolder: async () => "~/code/acme-web",
  reveal: async (_path: string) => undefined,
  onStatus: (_callback: Listener<ConnectionStatus>) => () => undefined,
  onLive: (callback: Listener<Live>) => {
    liveListeners.add(callback);
    return () => liveListeners.delete(callback);
  },
  onResync: (_callback: () => void) => () => undefined,
};
