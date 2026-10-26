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
  if (method === "GET" && url.pathname === "/chats") return ok(chats());
  if (method === "POST" && parts[0] === "chats" && parts[1] === "bot") {
    const chat = demo.chatIds[parts[2]];
    return request("POST", `/tasks/${chat}/messages`, body);
  }
  if (method === "POST" && parts[0] === "chats" && parts[2] === "read") return ok({ ok: true });
  if (parts[0] === "rooms" && parts[2] === "messages") {
    const messages = demo.roomMessages.get(parts[1]) ?? [];
    if (method === "GET") return ok(messages);
    const message = { id: messages.length + 1, room_id: parts[1], author: "user", text: body.text, depth: 0, at: new Date().toISOString() };
    messages.push(message);
    publish({ type: "room_message", message });
    setTimeout(() => {
      const reply = { id: messages.length + 1, room_id: parts[1], author: "chief", text: "Got it. (This is the design preview; in the app the lead would answer, and anyone you @mention.)", depth: 1, at: new Date().toISOString() };
      messages.push(reply);
      publish({ type: "room_message", message: reply });
    }, 1200);
    return ok(message);
  }
  if (method === "GET" && parts[0] === "bots" && parts[2] === "profile") {
    const threads = all.filter((t) => t.bot === parts[1] && t.kind === "thread").sort((a, b) => b.updated_at.localeCompare(a.updated_at));
    const outputs = threads.flatMap((t) => (demo.events.get(t.id) ?? []).filter((e) => e.kind === "handoff").flatMap((e) => (e.data.output as string[]).map((path) => ({ path, task_id: t.id, title: t.title, at: e.at }))));
    return ok({ bot: parts[1], working: threads.some((t) => t.status === "running"), last_active: threads[0]?.updated_at ?? null, threads, outputs });
  }
  if (method === "GET" && parts[0] === "tasks" && parts[2] === "status") {
    const task = demo.tasks.get(parts[1])!;
    const b = demo.bots.find((x) => x.name === task.bot)!;
    return ok({ task_id: task.id, bot: task.bot, backend: b.backend, plan: b.backend.split("-")[0], model: b.model, effort: b.effort, session_id: task.session_id ?? "019a2c3e-7f1d-7c4a-9e21-5b8d0c6f4a12", open: true, context_percent: 38.4, context_window: b.context_window, rollover_percent: 70, turns: task.turns ?? 12 });
  }
  if (method === "GET" && url.pathname === "/mcp/servers") return ok(mcpServers);
  if (method === "GET" && url.pathname === "/skills") return ok(skills);
  if (method === "GET" && url.pathname === "/mcp/installed") {
    return ok([
      { id: "a", source: "Claude Code", name: "blender", transport: "stdio", command: "uv", args: ["--directory", "~/blender_mcp/mcp", "run", "blender-mcp"], url: null, env_keys: ["BLENDER_MCP_PORT"], header_keys: [], in_crew: true },
      { id: "b", source: "Claude Code · Sky Islands", name: "UnityMCP", transport: "http", command: null, args: [], url: "http://127.0.0.1:8080/mcp", env_keys: [], header_keys: [], in_crew: false },
      { id: "c", source: "Codex", name: "gimp", transport: "stdio", command: "run-gimp-mcp.sh", args: [], url: null, env_keys: [], header_keys: [], in_crew: false },
    ]);
  }
  if (method === "GET" && url.pathname === "/mcp/registry") {
    return ok([{ name: "io.github.someone/figma-mcp", title: "Figma", description: "Read Figma files, frames and components.", version: "1.4.0", website: "https://github.com/someone/figma-mcp", options: [{ id: "package:0", label: "npm · figma-mcp 1.4.0", fields: [{ kind: "env", key: "FIGMA_TOKEN", description: "A personal access token", required: true, secret: true, default: null }] }, { id: "remote:0", label: "Remote · https://mcp.figma.example/mcp", fields: [] }] }]);
  }
  if (method === "POST" && parts[0] === "mcp" && parts[3] === "check") return ok({ ok: true, tools: ["get_scene_info", "create_object", "modify_object", "render", "execute_blender_code"], error: null });
  if (method === "GET" && url.pathname === "/routines") return ok(routines());
  if (method === "GET" && url.pathname === "/usage/summary") return ok(usageSummary(Number(url.searchParams.get("days") ?? 365)));
  if (method === "GET" && url.pathname === "/plans") return ok(plans);
  if (method === "GET" && url.pathname === "/reviews/inbox") return ok(reviewInbox);
  if (method === "GET" && url.pathname === "/reviews/repos") return ok({ "acme/web": "~/code/acme-web" });
  if (url.pathname === "/reviews/reviewers") return ok(method === "PUT" ? body : [{ backend: "claude", model: "sonnet", effort: "high" }, { backend: "codex", model: null, effort: "high" }]);
  if (method === "GET" && url.pathname === "/models") {
    return ok({
      claude: [
        { id: "default", label: "Default (recommended)", description: "Opus", efforts: ["low", "medium", "high", "max"], default_effort: null, default: true },
        { id: "sonnet", label: "Sonnet", description: "Best for everyday tasks", efforts: ["low", "medium", "high"], default_effort: null, default: false },
        { id: "haiku", label: "Haiku", description: "Fastest", efforts: [], default_effort: null, default: false },
      ],
      codex: [
        { id: "gpt-6.1-sol", label: "GPT-6.1-Sol", description: "", efforts: ["low", "medium", "high", "xhigh", "max", "ultra"], default_effort: "low", default: true },
        { id: "gpt-6-luna", label: "GPT-6-Luna", description: "", efforts: ["low", "medium", "high", "xhigh", "max"], default_effort: "medium", default: false },
      ],
    });
  }
  if (method === "POST" && url.pathname === "/attachments") {
    const id = Math.random().toString(16).slice(2, 14).padEnd(12, "0");
    const mime = /\.(png|jpe?g|gif|webp)$/i.test(body.name) ? "image/png" : "application/octet-stream";
    previewFiles.set(id, { name: body.name, mime, data: body.data });
    return ok({ id, name: body.name, path: `~/.crewbots/crew/attachments/${id}/${body.name}`, mime, size: Math.round((body.data.length * 3) / 4), kind: mime.startsWith("image/") ? "image" : "file" });
  }
  if (method === "GET" && parts[0] === "attachments") return ok(previewFiles.get(parts[1]));
  if (parts[0] === "reviews" && parts[1] === "drafts") {
    const id = Number(parts[2]);
    if (method === "DELETE") reviewDrafts.splice(reviewDrafts.findIndex((d) => d.id === id), 1);
    const draft = reviewDrafts.find((d) => d.id === id);
    if (method === "PUT" && draft) Object.assign(draft, { body: body.body, updated_at: new Date().toISOString() });
    return ok(method === "DELETE" ? { deleted: id } : draft);
  }
  if (parts[0] === "reviews" && parts[4] === "drafts") {
    if (method === "POST") {
      const at = new Date().toISOString();
      const draft = { id: reviewDrafts.length + 10, ...body, author: "user", task_id: null, commit_sha: "abc", created_at: at, updated_at: at };
      reviewDrafts.push(draft);
      return ok(draft);
    }
    return ok(reviewDrafts);
  }
  if (parts[0] === "reviews" && parts[4] === "submit") {
    const comments = reviewDrafts.length;
    reviewDrafts.splice(0);
    return ok({ url: "https://github.com/acme/web/pull/412#pullrequestreview-1", state: body.event === "APPROVE" ? "APPROVED" : body.event === "REQUEST_CHANGES" ? "CHANGES_REQUESTED" : "COMMENTED", comments });
  }
  if (parts[0] === "reviews" && parts[4] === "image") {
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120"><rect width="240" height="120" rx="16" fill="${url.searchParams.get("side") === "head" ? "#3987e5" : "#d95926"}"/><text x="120" y="68" font-size="22" text-anchor="middle" fill="white" font-family="sans-serif">${url.searchParams.get("side") === "head" ? "After" : "Before"}</text></svg>`;
    return ok({ mime: "image/svg+xml", data: btoa(svg) });
  }
  if (parts[0] === "reviews" && parts.length >= 4) {
    const pr = reviewInbox.direct.find((p) => p.number === Number(parts[3])) ?? reviewInbox.direct[0];
    if (parts[4] === "diff") return ok({ diff: (demo.diffs.get("t_csv0000002") ?? "") + "diff --git a/web/public/export-icon.png b/web/public/export-icon.png\nindex 1111111..2222222 100644\nBinary files a/web/public/export-icon.png and b/web/public/export-icon.png differ\n" });
    if (parts[4] === "review") return ok(demo.tasks.get("t_truncate03"));
    return ok({ ...pr, body: "Moves the export button into the toolbar and adds a test for the filters.\r\n\r\n1. Fixes part of #400.\r\n2. Moves the button:\r\n   - [x] Tests added\r\n   - [x] Screenshots updated\r\n", state: "OPEN", head: "toolbar-export", base: "main", head_sha: "abc", labels: [], files: [{ path: "web/src/reports/Toolbar.tsx", additions: 40, deletions: 12 }], checks: { total: 12, passed: 12, failed: 0 }, failing: [], issues: [], people: [pr.author, "lee", "kim"], checkout: null, reviews: pr.number === 412 ? demo.pullReviews : [] });
  }
  if (method === "GET" && parts[0] === "bots" && parts[2] === "card") {
    const b = demo.bots.find((x) => x.name === parts[1])!;
    return ok({ ...b, context_window: b.context_window ?? "auto", budget: null, instructions: `## Mission\n\nBe the ${b.title.toLowerCase()} of the crew.\n\n## Owns\n\n- What its role card says\n` });
  }
  if (method === "PUT" || (method === "POST" && (url.pathname === "/bots" || url.pathname === "/routines"))) return ok({});
  if (method === "GET" && parts[0] === "routines" && parts[2] === "file") {
    const r = routines().routines.find((x) => x.name === parts[1])!;
    return ok({ ...r, goal: "Write the standup for what the crew did since {{since}}.\n\n{{activity}}" });
  }
  if (method === "GET" && url.pathname === "/budgets") {
    return ok(demo.bots.map((b) => ({ bot: b.name, tokens: b.name === "engineer" ? 2_000_000 : null, per: "week", used: b.name === "engineer" ? 1_460_000 : 310_000, held: b.name === "chief" ? ["t_docs000001"] : [] })));
  }
  if (method === "POST" && parts[0] === "routines" && parts[1] === "starters") return ok({});
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
    const files = (body.attachments ?? []).map((a: string) => {
      const f = previewFiles.get(a)!;
      return { id: a, name: f.name, path: `~/.crewbots/crew/attachments/${a}/${f.name}`, mime: f.mime, size: Math.round((f.data.length * 3) / 4), kind: f.mime.startsWith("image/") ? "image" : "file" };
    });
    say(id, "message", { from: "user", text: body.text, ...(files.length ? { attachments: files } : {}) });
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

function chats() {
  const direct = demo.bots.map((b) => {
    const id = demo.chatIds[b.name];
    const events = demo.events.get(id) ?? [];
    const last = [...events].reverse().find((e) => e.kind === "text" || (e.kind === "message" && e.data.from === "user"));
    const family = [...demo.tasks.values()].filter((t) => t.parent_id === id || t.id === id);
    return {
      key: `bot:${b.name}`, kind: "direct", title: b.title, bot: b.name, task_id: id, room: null,
      last: last ? { author: last.kind === "text" ? b.name : "user", text: String(last.data.text), at: last.at } : null,
      unread: b.name === "engineer" ? 1 : 0, working: family.some((t) => t.status === "running") || b.name === "engineer",
      updated_at: last?.at ?? "",
    };
  });
  const groups = demo.rooms.map((room) => {
    const last = demo.roomMessages.get(room.id)?.at(-1);
    return { key: `room:${room.id}`, kind: "group", title: room.title, bot: null, task_id: null, room, last: last ? { author: last.author, text: last.text, at: last.at } : null, unread: 0, working: false, updated_at: last?.at ?? room.updated_at };
  });
  return [...direct, ...groups];
}

const reviewInbox = {
  direct: [
    { repo: "acme/web", number: 412, title: "Move the export button into the reports toolbar", url: "https://github.com/acme/web/pull/412", draft: false, author: "sam", author_avatar: null, created_at: new Date(Date.now() - 5 * 3600e3).toISOString(), updated_at: new Date(Date.now() - 3600e3).toISOString(), additions: 40, deletions: 12, changed_files: 3, review_decision: "REVIEW_REQUIRED", checks: "SUCCESS", reviewed_at: null },
    { repo: "acme/web", number: 409, title: "Fix the date filter's off-by-one at month boundaries", url: "https://github.com/acme/web/pull/409", draft: false, author: "lee", author_avatar: null, created_at: new Date(Date.now() - 30 * 3600e3).toISOString(), updated_at: new Date(Date.now() - 20 * 3600e3).toISOString(), additions: 18, deletions: 6, changed_files: 2, review_decision: "REVIEW_REQUIRED", checks: "FAILURE", reviewed_at: new Date().toISOString() },
  ],
  assigned: [
    { repo: "acme/web", number: 401, title: "Upgrade the charts library", url: "https://github.com/acme/web/pull/401", draft: false, author: "vir", author_avatar: null, created_at: new Date(Date.now() - 70 * 3600e3).toISOString(), updated_at: new Date(Date.now() - 60 * 3600e3).toISOString(), additions: 210, deletions: 180, changed_files: 9, review_decision: null, checks: "SUCCESS", reviewed_at: null },
  ],
  team: [
    { repo: "acme/api", number: 88, title: "Paginate the audit log endpoint", url: "https://github.com/acme/api/pull/88", draft: true, author: "kim", author_avatar: null, created_at: new Date(Date.now() - 50 * 3600e3).toISOString(), updated_at: new Date(Date.now() - 48 * 3600e3).toISOString(), additions: 120, deletions: 30, changed_files: 6, review_decision: null, checks: "PENDING", reviewed_at: null },
  ],
  mine: [],
  error: null,
};

function usageSummary(days: number) {
  // Quiet half a year ago, busier lately, with weekends mostly off; each day the same whatever the range.
  const daily = Array.from({ length: days }, (_, i) => {
    const date = new Date(Date.now() - (days - 1 - i) * 86400e3);
    const n = Math.floor(date.getTime() / 86400e3);
    const ramp = Math.max(0, 1 - (days - 1 - i) / 180);
    const weekend = date.getDay() === 0 || date.getDay() === 6;
    const on = ramp > 0 && Math.abs(Math.sin(n * 12.9898) * 43758.5453) % 1 < (weekend ? 0.2 : 0.85) * ramp + 0.1;
    const size = on ? Math.round(ramp * (180_000 + 420_000 * (Math.abs(Math.sin(n * 78.233) * 9973) % 1))) : 0;
    return { date: date.toISOString().slice(0, 10), claude: Math.round(size * 0.45), codex: Math.round(size * 0.55) };
  });
  const totals = { claude: daily.reduce((a, d) => a + d.claude, 0), codex: daily.reduce((a, d) => a + d.codex, 0) };
  const peak = daily.reduce((a, d) => (d.claude + d.codex > a.claude + a.codex ? d : a));
  return {
    days, daily, totals, turns: 1_412,
    peak: { date: peak.date, tokens: peak.claude + peak.codex },
    longest_turn: { seconds: 2_710, task_id: "t_csv0000002", title: "Add CSV export to the reports page" },
    streak: { longest: 12, current: 4 },
    models: [
      { plan: "codex", model: "gpt-6.1-sol", tokens: Math.round(totals.codex * 0.8), input: Math.round(totals.codex * 3.1), cached: Math.round(totals.codex * 2.4) },
      { plan: "claude", model: "opus", tokens: Math.round(totals.claude * 0.55), input: Math.round(totals.claude * 2.6), cached: Math.round(totals.claude * 2.2) },
      { plan: "claude", model: "sonnet", tokens: Math.round(totals.claude * 0.45), input: Math.round(totals.claude * 1.9), cached: Math.round(totals.claude * 1.5) },
      { plan: "codex", model: "gpt-6-luna", tokens: Math.round(totals.codex * 0.2), input: Math.round(totals.codex * 0.7), cached: Math.round(totals.codex * 0.4) },
    ],
    bots: [
      { bot: "engineer", tokens: Math.round(totals.codex * 0.7), input: Math.round(totals.codex * 0.7) * 4, cached: Math.round(totals.codex * 0.7) * 3 }, { bot: "chief", tokens: Math.round(totals.claude * 0.5), input: Math.round(totals.claude * 0.5) * 4, cached: Math.round(totals.claude * 0.5) * 3 },
      { bot: "reviewer", tokens: Math.round(totals.claude * 0.3), input: Math.round(totals.claude * 0.3) * 4, cached: Math.round(totals.claude * 0.3) * 3 }, { bot: "PR reviewer", tokens: Math.round((totals.claude + totals.codex) * 0.12) },
      { bot: "critic", tokens: Math.round(totals.codex * 0.18), input: Math.round(totals.codex * 0.18) * 4, cached: Math.round(totals.codex * 0.18) * 3 },
    ],
    efforts: [{ effort: "high", turns: 812 }, { effort: "medium", turns: 433 }, { effort: "xhigh", turns: 167 }],
    counts: { threads: 64, reviews: 23, routine_runs: 41, room_messages: 318 },
    cache: { input: (totals.claude + totals.codex) * 4, cached: (totals.claude + totals.codex) * 3 },
  };
}

const plans = {
  claude: {
    available: true, error: null, notes: [], checked_at: Date.now() / 1000,
    windows: [
      { name: "Session", used_percent: 37, resets_at: demo.resetsAt },
      { name: "Week · all models", used_percent: 12, resets_at: demo.resetsAt + 3 * 86400 },
      { name: "Week · Fable", used_percent: 4, resets_at: demo.resetsAt + 3 * 86400 },
    ],
  },
  codex: {
    available: true, error: null, notes: ["12,400 credits"], checked_at: Date.now() / 1000,
    windows: [
      { name: "5 hours", used_percent: 84, resets_at: demo.resetsAt - 3600 },
      { name: "Week", used_percent: 11, resets_at: demo.resetsAt + 5 * 86400 },
    ],
  },
};

const previewFiles = new Map<string, { name: string; mime: string; data: string }>();

const reviewDrafts: { id: number; path: string; line: number; side: string; body: string; author: string; task_id: string | null; commit_sha: string; created_at: string; updated_at: string }[] = [
  { id: 1, path: "server/reports.py", line: 64, side: "RIGHT", body: "`filename` is fixed, so two exports in a row overwrite each other in the browser's downloads. Put the date in it.", author: "reviewer:claude:sonnet:high", task_id: null, commit_sha: "abc", created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
];

const mcpServers = [
  { name: "blender", transport: "stdio", command: "uv", args: ["--directory", "~/blender_mcp/mcp", "run", "blender-mcp"], url: null, env_keys: ["BLENDER_MCP_PORT"], header_keys: [], bots: ["engineer"] },
  { name: "davinci-resolve", transport: "stdio", command: "/bin/sh", args: ["start-resolve-mcp.sh"], url: null, env_keys: ["RESOLVE_SCRIPT_API"], header_keys: [], bots: [] },
];

const skills = [
  { name: "arena", description: "Two or more agents attempt the same task independently; pick the best.", source: "crew", path: "~/.crewbots/crew/skills/arena/SKILL.md", used: true, same_as: null },
  { name: "interrogate", description: "Question the user until the goal is unambiguous.", source: "crew", path: "~/.crewbots/crew/skills/interrogate/SKILL.md", used: true, same_as: null },
  { name: "pdf", description: "Read, fill and make PDF files.", source: "~/.claude/skills", path: "~/.claude/skills/synced/x/pdf/SKILL.md", used: true, same_as: null },
  { name: "unity-mcp-orchestrator", description: "Orchestrate the Unity Editor through MCP for Unity.", source: "~/.claude/skills", path: "~/.claude/skills/unity-mcp-skill/SKILL.md", used: true, same_as: null },
  { name: "graphify", description: "Turn a codebase into a knowledge graph.", source: "~/.claude/skills", path: "~/.claude/skills/graphify/SKILL.md", used: true, same_as: null },
  { name: "graphify", description: "Turn a codebase into a knowledge graph.", source: "~/.codex/skills", path: "~/.codex/skills/graphify/SKILL.md", used: false, same_as: "~/.claude/skills" },
];

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
    crew_error: null,
    starters: [{ name: "ci-failed", description: "Works out why checks failed on one of your pull requests, and what would fix it.", when: "on github.ci_failed in owner/name" }],
  };
}

export const previewBridge = {
  status: async () => status,
  request,
  pickFolder: async () => "~/code/acme-web",
  reveal: async (_path: string) => undefined,
  openPath: async (_path: string) => undefined,
  openUrl: async (url: string) => void window.open(url, "_blank"),
  startAtLogin: async (enabled?: boolean) => enabled ?? true,
  dictation: browserDictation(),
  onStatus: (_callback: Listener<ConnectionStatus>) => () => undefined,
  onLive: (callback: Listener<Live>) => {
    liveListeners.add(callback);
    return () => liveListeners.delete(callback);
  },
  onResync: (_callback: () => void) => () => undefined,
};

/** In a browser, dictation uses its own speech recognition where it has one (Chrome does). */
function browserDictation() {
  const listeners = new Set<(text: string) => void>();
  const stopped = new Set<(error: string | null) => void>();
  let recognition: any = null;
  return {
    start: async () => {
      const Recognition = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;
      if (!Recognition) throw new Error("This browser can't dictate");
      recognition = new Recognition();
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.onresult = (event: any) => {
        const text = Array.from(event.results as ArrayLike<any>).map((r) => r[0].transcript).join("");
        listeners.forEach((l) => l(text.trim()));
      };
      recognition.onend = () => stopped.forEach((l) => l(null));
      recognition.onerror = (event: any) => stopped.forEach((l) => l(String(event.error)));
      recognition.start();
    },
    stop: async () => recognition?.stop(),
    onHeard: (callback: (text: string) => void) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    onStopped: (callback: (error: string | null) => void) => {
      stopped.add(callback);
      return () => stopped.delete(callback);
    },
  };
}
