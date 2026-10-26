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
  if (method === "GET" && url.pathname === "/routines") return ok(routines());
  if (method === "GET" && url.pathname === "/usage") return ok(usage(Number(url.searchParams.get("days") ?? 14)));
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
  if (parts[0] === "reviews" && parts.length >= 4) {
    const pr = reviewInbox.direct.find((p) => p.number === Number(parts[3])) ?? reviewInbox.direct[0];
    if (parts[4] === "diff") return ok({ diff: demo.diffs.get("t_csv0000002") ?? "" });
    if (parts[4] === "review") return ok(demo.tasks.get("t_truncate03"));
    return ok({ ...pr, body: "Moves the export button into the toolbar and adds a test for the filters.\n\n- [x] Tests added\n- [x] Screenshots updated", state: "OPEN", head: "toolbar-export", base: "main", head_sha: "abc", labels: [], files: [{ path: "web/src/reports/Toolbar.tsx", additions: 40, deletions: 12 }], checks: { total: 12, passed: 12, failed: 0 }, failing: [], issues: [], checkout: null, reviews: [] });
  }
  if (method === "GET" && parts[0] === "bots" && parts[2] === "card") {
    const b = demo.bots.find((x) => x.name === parts[1])!;
    return ok({ ...b, budget: null, instructions: `## Mission\n\nBe the ${b.title.toLowerCase()} of the crew.\n\n## Owns\n\n- What its role card says\n` });
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

function usage(days: number) {
  const dates = Array.from({ length: days }, (_, i) => new Date(Date.now() - (days - 1 - i) * 86400e3).toISOString().slice(0, 10));
  const wave = (scale: number, phase: number) => dates.map((_, i) => (i % 7 === 5 || i % 7 === 6 ? 0 : Math.round(scale * (0.6 + 0.4 * Math.sin(i / 2 + phase)))));
  const bots = [
    { bot: "chief", by_day: wave(42_000, 0) }, { bot: "engineer", by_day: wave(180_000, 1) },
    { bot: "reviewer", by_day: wave(60_000, 2) }, { bot: "critic", by_day: wave(55_000, 3) },
  ];
  return { days: dates, bots: bots.map((b) => ({ ...b, turns: b.by_day.map((v) => Math.round(v / 9000)), total: b.by_day.reduce((a, v) => a + v, 0) })) };
}

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
