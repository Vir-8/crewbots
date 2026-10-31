// The bridge the UI uses outside Tauri: it answers the API from the demo crew, and plays a little live activity, so
// the UI can be designed and checked in an ordinary browser.

import { API_SCHEMA, API_VERSION } from "../api-types";
import type { Bridge } from "../bridge";
import type { ConnectionStatus, Live, StoredEvent, Task } from "../types";
import * as demo from "./demo";
import { landedRequest } from "./demo/landed";
import { projectRequest } from "./demo/projects";
import { settingsRequest } from "./demo/settings";
import { toolsRequest } from "./demo/tools";
import { chats, crewState, pins, plans, previewFiles, previewModels, setPins, reviewDrafts, reviewInbox, skills, status, usageSummary } from "./fixtures";

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
let previewZone: string | null = null;
let step = 0;
let ticking: number | undefined; // from the first listener on: nothing runs just because this module was imported
const still = new URLSearchParams(globalThis.location?.search ?? "").has("still"); // ?still: no live activity, for comparing screens
const tick = () => {
  if (still) return;
  ticking ??= window.setInterval(() => {
    const s = steps[step++ % steps.length];
    say(demo.live.taskId, "tool_call", { id: `live-${step}`, name: s.name, input: s.input });
  }, 3500);
};

// Group chat messages are numbered across every room, as the daemon numbers them.
const nextMessageId = () => Math.max(0, ...[...demo.roomMessages.values()].flat().map((m) => m.id)) + 1;
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
  if (method === "POST" && parts[0] === "push-proposals" && parts[2] === "push") {
    const proposal = allEvents.find((event) => event.id === Number(parts[1]) && event.kind === "push_proposed");
    if (!proposal) return missing("push proposal");
    if (allEvents.some((event) => event.kind === "push_result" && event.data.proposal === proposal.id)) return missing("pending push proposal");
    const pushed = Array.isArray(proposal.data.commits) && proposal.data.commits.length > 0;
    const result = { proposal: proposal.id, ok: true, pushed, reason: pushed ? "Push completed." : "Everything up-to-date. Nothing was pushed." };
    say(proposal.task_id, "push_result", result);
    return ok(result);
  }
  if (url.pathname === "/update") return ok(previewUpdate(method === "POST"));
  if (method === "GET" && url.pathname === "/crew") return ok(crewState);
  if (method === "POST" && (url.pathname === "/crew/pause" || url.pathname === "/crew/resume")) {
    crewState.paused = url.pathname === "/crew/pause";
    setTimeout(() => publish({ type: "crew", crew: { ...crewState } as never }), 0);
    return ok({ ...crewState });
  }
  if (url.pathname === "/crew/timezone") {
    if (method === "PUT") previewZone = body?.timezone ?? null;
    return ok({ timezone: previewZone ?? "Europe/London", system: "Europe/London", custom: previewZone !== null });
  }
  if (method === "GET" && url.pathname === "/version") return ok({ api: API_VERSION, schema_hash: API_SCHEMA, build: "preview", source_changed: false });
  if (url.pathname === "/crew/restart") {
    crewState.restarting = method === "POST" && body?.when_idle !== false;
    return ok({ ...crewState });
  }
  const projectsAnswer = projectRequest(method, url.pathname, parts, body, { publish, say });
  if (projectsAnswer) return projectsAnswer;
  const landedAnswer = landedRequest(method, url.pathname, parts, allEvents, { publish, say });
  if (landedAnswer) return landedAnswer;
  const settingsAnswer = settingsRequest(method, url.pathname, parts, body);
  if (settingsAnswer) return settingsAnswer;
  const toolsAnswer = await toolsRequest(method, url.pathname, parts, body, (name) => request("POST", `/routines/${name}/run`));
  if (toolsAnswer) return toolsAnswer;
  if (method === "GET" && url.pathname === "/chats") return ok(chats());
  if (url.pathname === "/pins") {
    if (method === "PUT") {
      setPins([...new Set<string>(body.pins)]);
      setTimeout(() => publish({ type: "pins", pins }), 0);
    }
    return ok({ pins });
  }
  if (method === "PUT" && parts[0] === "chats" && parts[1] === "bot" && parts[3] === "mute") {
    if (body.muted) demo.mutedBots.add(parts[2]); else demo.mutedBots.delete(parts[2]);
    publish({ type: "chat_mute", bot: parts[2], muted: body.muted });
    return ok({ muted: body.muted });
  }
  if (method === "POST" && parts[0] === "chats" && parts[1] === "bot" && parts[3] === "threads") {
    const thread = demo.task(`t_new${Date.now().toString(16).slice(-7)}`, parts[2], body.goal, { parent_id: demo.chatIds[parts[2]], status: "running", reported: false });
    say(thread.id, "message", { from: "user", text: body.goal });
    publishTask(thread);
    return ok(thread);
  }
  if (method === "POST" && parts[0] === "chats" && parts[1] === "bot" && parts[3] === "notes") {
    const title = String(body.text).split("\n")[0].slice(0, 80);
    say(demo.chatIds[parts[2]], "memory", { action: "remember", scope: parts[2], title, body: body.text, by: "user" });
    return ok({ title, path: `~/.crewbots/crew/memory/${parts[2]}/notes/${title}.md` });
  }
  if (method === "POST" && parts[0] === "chats" && parts[1] === "bot") {
    const chat = demo.chatIds[parts[2]];
    return request("POST", `/tasks/${chat}/messages`, body);
  }
  if (method === "POST" && parts[0] === "chats" && parts[2] === "read") return ok({ ok: true });
  if (method === "GET" && url.pathname === "/rooms") return ok(demo.rooms);
  if (method === "POST" && parts[0] === "proactive" && parts[2] === "react") return ok({ id: Number(parts[1]), reaction: body.reaction });
  if (parts[0] === "rooms" && parts.length === 2 && (method === "PATCH" || method === "DELETE")) {
    const at = demo.rooms.findIndex((r) => r.id === parts[1]);
    if (at < 0) return missing("group chat");
    if (method === "DELETE") {
      demo.rooms.splice(at, 1);
      demo.roomMessages.delete(parts[1]);
      publish({ type: "room_gone", room_id: parts[1] });
      return ok(null);
    }
    const { archived, ...fields } = body ?? {};
    const room = Object.assign(demo.rooms[at], fields, archived === undefined ? {} : { archived_at: archived ? new Date().toISOString() : null });
    publish({ type: "room", room });
    return ok(room);
  }
  if (method === "POST" && parts[0] === "rooms" && parts[2] === "continue") {
    const room = demo.rooms.find((r) => r.id === parts[1])!;
    const waiting = room.stopped?.waiting ?? [];
    room.stopped = null;
    publish({ type: "room", room });
    const messages = demo.roomMessages.get(room.id)!;
    for (const [i, author] of waiting.entries()) {
      setTimeout(() => {
        const reply = { id: nextMessageId(), room_id: room.id, author, text: "Carrying on. (This is the design preview; in the app the bots pick up where they stopped.)", depth: 1, at: new Date().toISOString() };
        messages.push(reply);
        publish({ type: "room_message", message: reply });
      }, 1200 * (i + 1));
    }
    return ok(room);
  }
  if (parts[0] === "rooms" && parts[2] === "messages") {
    const messages = demo.roomMessages.get(parts[1]) ?? [];
    if (method === "GET") return ok([...messages]); // a copy, as over the wire: what comes later arrives live
    const stopped = demo.rooms.find((r) => r.id === parts[1]);
    if (stopped?.stopped) {
      stopped.stopped = null;
      publish({ type: "room", room: stopped });
    }
    const message = { id: nextMessageId(), room_id: parts[1], author: "user", text: body.text, depth: 0, at: new Date().toISOString() };
    messages.push(message);
    publish({ type: "room_message", message });
    setTimeout(() => {
      const reply = { id: nextMessageId(), room_id: parts[1], author: "chief", text: "Got it. (This is the design preview; in the app the lead would answer, and anyone you @mention.)", depth: 1, at: new Date().toISOString() };
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
    return ok({ task_id: task.id, bot: task.bot, backend: b.backend, plan: b.backend.split("-")[0], model: b.model, effort: b.effort, session_id: task.session_id ?? "019a2c3e-7f1d-7c4a-9e21-5b8d0c6f4a12", open: true, context_percent: 38.4, context_window: b.context_window, rollover_percent: 70, turns: task.turns ?? 12, background: "running, 64% left in the 5 hours window" });
  }
  if (method === "GET" && url.pathname === "/skills") return ok(skills);
  if (method === "GET" && url.pathname === "/usage/summary") return ok(usageSummary(Number(url.searchParams.get("days") ?? 365)));
  if (method === "GET" && url.pathname === "/plans") return ok(plans);
  if (method === "GET" && url.pathname === "/reviews/inbox") return ok(reviewInbox);
  if (url.pathname === "/reviews/reviewers") return ok(method === "PUT" ? body : [{ backend: "claude", model: "sonnet", effort: "high" }, { backend: "codex", model: null, effort: "high" }]);
  if (method === "GET" && url.pathname === "/models") return ok(previewModels);
  if (method === "POST" && url.pathname === "/attachments") {
    const id = Math.random().toString(16).slice(2, 14).padEnd(12, "0");
    const ext = String(body.name).split(".").pop()!.toLowerCase();
    const mime = MIMES[ext] ?? "application/octet-stream";
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
    if (parts[4] === "diff") return ok({ diff: (demo.diffs.get("t_csv0000002") ?? "") + bigDiff() + "diff --git a/web/public/export-icon.png b/web/public/export-icon.png\nindex 1111111..2222222 100644\nBinary files a/web/public/export-icon.png and b/web/public/export-icon.png differ\n" });
    if (parts[4] === "review") return ok(demo.tasks.get("t_truncate03"));
    return ok({ ...pr, body: "Moves the export button into the toolbar and adds a test for the filters.\r\n\r\n1. Fixes part of #400.\r\n2. Moves the button:\r\n   - [x] Tests added\r\n   - [x] Screenshots updated\r\n", state: "OPEN", head: "toolbar-export", base: "main", head_sha: "abc", labels: [], files: [{ path: "web/src/reports/Toolbar.tsx", additions: 40, deletions: 12 }], checks: { total: 12, passed: 12, failed: 0 }, failing: [], issues: [], people: [pr.author, "lee", "kim"], checkout: null, reviews: pr.number === 412 ? demo.pullReviews : [] });
  }
  if (method === "GET" && parts[0] === "bots" && parts[2] === "card") {
    const b = demo.bots.find((x) => x.name === parts[1])!;
    return ok({ ...b, context_window: b.context_window ?? "auto", budget: null, instructions: `## Mission\n\nBe the ${b.title.toLowerCase()} of the crew.\n\n## Owns\n\n- What its role card says\n` });
  }
  // Nothing reads what saving a card or making a bot or routine answers.
  if ((method === "PUT" && parts[0] === "bots" && parts[2] === "card") || (method === "POST" && (url.pathname === "/bots" || url.pathname === "/routines"))) return ok({});
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
    if (task.kind === "seat") {
      const question = allEvents.find((e) => e.kind === "seat_escalation" && e.data.task === id && e.data.question_id === body.question_id);
      const closed = allEvents.some((e) => ["seat_escalation_closed", "seat_escalation_answer"].includes(e.kind) && e.data.question_id === body.question_id);
      if (!question || closed) return { ok: false, status: 403, data: { detail: "This question is closed or has been replaced. Please use the current question." } };
    }
    const files = (body.attachments ?? []).map((a: string) => {
      const f = previewFiles.get(a)!;
      return { id: a, name: f.name, path: `~/.crewbots/crew/attachments/${a}/${f.name}`, mime: f.mime, size: Math.round((f.data.length * 3) / 4), kind: f.mime.startsWith("image/") ? "image" : "file" };
    });
    say(id, "message", { from: "user", text: body.text, ...(files.length ? { attachments: files } : {}) });
    if (task.kind === "seat") {
      for (const [chatId, events] of demo.events) {
        const question = events.find((e) => e.kind === "seat_escalation" && e.data.task === id && e.data.question_id === body.question_id);
        if (question) say(chatId, "seat_escalation_answer", { question_id: question.data.question_id, text: body.text });
      }
    }
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
  /** ?alreadyon marks every proposal as already part of main, to see that card. */
  if (method === "GET" && action === "merge-status") return ok({ already_on: new URLSearchParams(location.search).has("alreadyon") ? "main" : null });
  if (method === "POST" && action === "withdraw-merge") {
    const reason = String(body?.reason ?? "").trim();
    if (!reason || task.merge_state !== "proposed") return { ok: false, status: 400, data: { detail: "A proposed merge and reason are required" } };
    const updated = update(id, { merge_state: null });
    say(id, "merge_withdrawn", { by: "user", reason });
    return ok(updated);
  }
  return missing(`${method} ${path}`);
}

const MIMES: Record<string, string> = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", webp: "image/webp", svg: "image/svg+xml", mp4: "video/mp4", mov: "video/quicktime", webm: "video/webm", mp3: "audio/mpeg", wav: "audio/wav", m4a: "audio/mp4", pdf: "application/pdf", html: "text/html", txt: "text/plain", md: "text/markdown", csv: "text/csv", json: "application/json", py: "text/x-python" };

/** ?bigdiff=300 adds that many files of 60 lines each to a pull request's diff, to see how a long one opens. */
function bigDiff(): string {
  const files = Number(new URLSearchParams(location.search).get("bigdiff") ?? 0);
  let out = "";
  for (let f = 0; f < files; f++) {
    const rows = Array.from({ length: 60 }, (_, i) => `${i % 3 === 0 ? "+" : i % 7 === 0 ? "-" : " "}    value_${f}_${i} = compute(${i}, "row ${i} of file ${f}")`);
    out += `diff --git a/gen/file_${f}.py b/gen/file_${f}.py\n--- a/gen/file_${f}.py\n+++ b/gen/file_${f}.py\n@@ -1,50 +1,60 @@\n${rows.join("\n")}\n`;
  }
  return out;
}

// An update to crewbots: three changes waiting; starting it walks through the build's steps, a few seconds each, and
// ends with a build for the app to install.
const UPDATE_STEPS = ["Checks", "Building the app"];
let updateStarted = 0;
function previewUpdate(starting: boolean) {
  if (starting && !updateStarted) updateStarted = Date.now();
  const at = updateStarted ? Math.floor((Date.now() - updateStarted) / 3000) : -1;
  const release = at < 0 ? {} : at < UPDATE_STEPS.length ? { state: "running", step: UPDATE_STEPS[at], commit: "f00dfeed1234", started_at: Math.floor(updateStarted / 1000), expected: UPDATE_STEPS.length * 3 } : { state: "built", step: "Built", commit: "f00dfeed1234", bundle: "~/.crewbots/build/app/release/bundle/macos/crewbots.app" };
  return {
    app: __APP_COMMIT__, daemon: __APP_COMMIT__, head: "f00dfeed1234", available: true, pull: true, note: null, release,
    needs: { app: true, daemon: true },
    commits: [
      { sha: "f00dfee", at: "", subject: "Check for updates and update from the app" },
      { sha: "a1b2c3d", at: "", subject: "Pin chats to the top of the sidebar in your own order" },
      { sha: "0ddba11", at: "", subject: "Find with ⌘F in chats, group chats and the thread beside them" },
    ],
  };
}

/** A file the preview draws: a demo file, or one you attached here. */
function previewFileSrc(path: string): string {
  const demoFile = demo.demoFiles.get(path);
  if (demoFile !== undefined) return demoFile;
  const attached = /attachments\/([0-9a-f]{12})\//.exec(path);
  const f = attached && previewFiles.get(attached[1]);
  return f ? `data:${f.mime};base64,${f.data}` : "";
}

async function previewThumbnail(path: string): Promise<string | null> {
  return demo.demoFiles.has(`${path}.png`) ? `${path}.png` : null;
}

export const previewBridge = {
  status: async () => status,
  request,
  pickFolder: async () => "~/code/acme-web",
  reveal: async (_path: string) => undefined,
  home: async () => "/Users/pat",
  openPath: async (_path: string) => undefined,
  fileSrc: (path: string) => previewFileSrc(path),
  showFiles: async (_paths: string[]) => undefined,
  openFile: async (_path: string) => undefined,
  thumbnail: async (path: string, _size: number) => previewThumbnail(path),
  openUrl: async (url: string) => void window.open(url, "_blank"),
  startAtLogin: async (enabled?: boolean) => enabled ?? true,
  appInfo: async () => ({ installed_newer: new URLSearchParams(location.search).has("update"), relaunch_failure: null }), // ?update shows the banner
  restartApp: async () => location.assign(location.pathname),
  installUpdate: async (_bundle: string) => ({ path: "/Applications/crewbots.app", warning: null }),
  dictation: browserDictation(),
  onStatus: (_callback: Listener<ConnectionStatus>) => () => undefined,
  onLive: (callback: Listener<Live>) => {
    liveListeners.add(callback);
    tick();
    return () => liveListeners.delete(callback);
  },
  onResync: (_callback: () => void) => () => undefined,
  onMenu: (_callback: (item: string) => void) => () => undefined, // a browser has no app menu: ⌘K has the same
} satisfies Bridge;

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

