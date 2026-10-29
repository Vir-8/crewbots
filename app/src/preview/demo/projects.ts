// Projects, what Claude Code and Codex know, the bots' pages, and what a bot asks you to allow, for the preview's bridge.

import type { FoundOut } from "../../api-types";
import type { Live as LiveMessage } from "../../types";
import { chatIds } from "./chats";
import { emit, message, say as said, task, tasks } from "./make";

type Live = { publish: (message: LiveMessage) => void; say: (id: string, kind: string, data: Record<string, unknown>) => void };
const ok = (data: unknown) => ({ ok: true, status: 200, data });
const missing = (what: string) => ({ ok: false, status: 404, data: { detail: `no ${what}` } });

// The demo's, kept while the preview is open. ?first shows the chief's first word, before the crew's been started off.
let started = !new URLSearchParams(globalThis.location?.search ?? "").has("first");
if (!started) {
  // The chief's first word, as the daemon records it: a few messages, and the offer under them.
  for (const text of ["Hey! I'm your Chief of Staff.", "Message me anytime. I'll keep things moving, even when we're not talking, and check in with updates or questions.", "Want to give me a name?"]) {
    emit(chatIds.chief, "text", { text, greeting: true });
  }
  emit(chatIds.chief, "onboarding", { offer: "bring_in" });
}
const projects = [
  { name: "acme-web", folder: "/Users/you/code/acme-web", about: "Acme's customer site: Next.js, deployed from main by you.", isolation: "worktree", missing: false },
  { name: "export-service", folder: "/Users/you/code/export-service", about: "The service behind report exports.", isolation: "worktree", missing: false },
  { name: "glider", folder: "/Users/you/Games/Glider", about: "A Unity URP glider game.", isolation: "branch", missing: false },
];
const found: FoundOut = {
  folders: [
    { folder: "/Users/you/code/acme-web", name: "acme-web", about: "Acme's customer site.", sources: ["claude", "codex"], sessions: 42, last_used: new Date(Date.now() - 2 * 3600_000).toISOString(), memories: [{ path: "/Users/you/.claude/projects/acme-web/memory/MEMORY.md", source: "claude", title: "Its index", brought_at: new Date(Date.now() - 3 * 86400_000).toISOString() }, { path: "/Users/you/.claude/projects/acme-web/memory/deploys.md", source: "claude", title: "Deploys go out from main on Fridays only", brought_at: new Date(Date.now() - 3 * 86400_000).toISOString(), changed: true }], project: "acme-web" },
    { folder: "/Users/you/Games/Sky Islands", name: "sky-islands", about: "A cozy third-person glider game in Unity.", sources: ["claude"], sessions: 24, last_used: new Date(Date.now() - 26 * 3600_000).toISOString(), memories: [{ path: "/Users/you/.claude/projects/sky/memory/MEMORY.md", source: "claude", title: "Its index" }, { path: "/Users/you/.claude/projects/sky/memory/art.md", source: "claude", title: "Stylized fantasy art; no painted pavements" }, { path: "/Users/you/.claude/projects/sky/memory/demo.md", source: "claude", title: "The Steam demo's status" }], project: null },
    { folder: "/Users/you/Documents/Trading", name: "trading", about: "", sources: ["codex"], sessions: 27, last_used: new Date(Date.now() - 4 * 86400_000).toISOString(), memories: [], project: null },
  ],
  memories: [],
};

// A thread waiting on your permission: asked there, shown in the chief's chat too, and answered from either.
const trailer = task("t_trailer001", "engineer", "Render the launch trailer", { parent_id: chatIds.chief, created_by: "chief", status: "needs_input", workspace: null });
message(trailer.id, "chief", "Render the 30-second launch trailer from the gameplay captures.");
said(trailer.id, "The captures are cut. The frames and the final render come to about 6 GB, too big for the repo.");
const asked = {
  permission: 1, kind: "folder", bot: "engineer", task: trailer.id, folder: "/Users/you/Movies/Launch Trailer",
  what: "Write the trailer's frames and the final cut into ~/Movies/Launch Trailer", why: "They're about 6 GB: too big for the repo, and they belong with your other renders.",
};
const askedIn = [trailer.id, chatIds.chief];
for (const id of askedIn) emit(id, "permission", asked);

const pages: Record<string, Record<string, { text: string; updated: string }>> = {
  chief: {
    Scratchpad: { text: "## Top of mind\n\n- The CSV export ships Friday: [[Launch plan]]\n\n## In flight\n\n- engineer: the export endpoint and button (t_csv0000002), in review with the critic\n\n## Waiting on you\n\n- Whether release days belong in the standards\n\n## Next look\n\n- The critic's review, this afternoon", updated: new Date(Date.now() - 40 * 60_000).toISOString() },
    "Launch plan": { text: "# Launch plan\n\n1. Export endpoint and button, with tests (engineer)\n2. A break-it pass on the filters (critic)\n3. Your go-ahead to merge\n\nBack to the [[Scratchpad]].", updated: new Date(Date.now() - 3 * 3600_000).toISOString() },
  },
};

export function projectRequest(method: string, pathname: string, parts: string[], body: any, live: Live) {
  const { publish, say } = live;
  if (pathname === "/projects" && method === "GET") return ok({ projects, errors: {}, started });
  if (pathname === "/projects/started" && method === "POST") {
    started = true;
    say(chatIds.chief, "onboarding", { declined: true });
    return ok(null);
  }
  if (pathname === "/projects/found") return ok(found);
  if (method === "POST" && parts[0] === "permissions" && Number(parts[1]) === asked.permission) {
    for (const id of askedIn) say(id, "permission_answer", { permission: asked.permission, allowed: body.allowed });
    publish({ type: "task", task: { ...tasks.get(trailer.id)!, status: body.allowed ? "running" : "idle" } });
    return ok({ id: asked.permission, task_id: trailer.id, kind: asked.kind, folder: asked.folder, what: asked.what, why: asked.why, asked_at: "", allowed: body.allowed, answered_at: new Date().toISOString() });
  }
  if (pathname === "/projects/bring-in" && method === "POST") {
    for (const p of body.projects) projects.push({ ...p, isolation: "worktree", missing: false });
    for (const f of found.folders) if (body.projects.some((p: { folder: string }) => p.folder === f.folder)) f.project = f.name;
    for (const m of [...found.memories, ...found.folders.flatMap((f) => f.memories)]) {
      if (body.memories.includes(m.path)) Object.assign(m, { brought_at: new Date().toISOString(), changed: false });
    }
    started = true;
    const chat = chatIds.chief;
    say(chat, "onboarding", { brought: { projects: body.projects.map((p: { name: string }) => p.name), memories: body.memories.length, thread: null } });
    if (body.memories.length) {
      setTimeout(() => say(chat, "text", { text: "I've kept what's worth keeping, and put the projects on my [[Scratchpad]]. (This is the design preview; in the app I'd learn from them in a thread.) What's top of mind?" }), 1500);
    }
    return ok({ projects: body.projects.map((p: object) => ({ ...p, missing: false })), memories: body.memories.length, task_id: body.memories.length ? chat : null });
  }
  if (parts[0] === "projects" && parts.length === 2) {
    const at = projects.findIndex((p) => p.name === parts[1]);
    if (method === "DELETE") {
      if (at >= 0) projects.splice(at, 1);
      return ok(null);
    }
    const project = { name: parts[1], folder: body.folder, about: body.about, isolation: body.isolation ?? projects[at]?.isolation ?? "worktree", missing: false };
    if (at >= 0) projects[at] = project;
    else projects.push(project);
    return ok(project);
  }
  if (parts[0] === "bots" && parts[2] === "pages") {
    const mine = (pages[parts[1]] ??= {});
    const page = (name: string) => ({ name, path: `/Users/you/Documents/Crewbots/${parts[1]}/${name}.md`, updated: mine[name].updated });
    if (parts.length === 3) return ok(Object.keys(mine).sort((a, b) => (a === "Scratchpad" ? -1 : b === "Scratchpad" ? 1 : 0)).map((name) => ({ ...page(name), text: null })));
    const asked = decodeURIComponent(parts.slice(3).join("/"));
    const name = Object.keys(mine).find((n) => n.toLowerCase() === asked.toLowerCase()) ?? asked;
    if (method === "PUT") {
      mine[name] = { text: body.text, updated: new Date().toISOString() };
      setTimeout(() => publish({ type: "page", bot: parts[1], page: name }), 0);
    }
    return mine[name] ? ok({ ...page(name), text: mine[name].text }) : missing(`page ${name}`);
  }
  return null;
}
