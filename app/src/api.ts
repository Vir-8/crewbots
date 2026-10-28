import type {
  Bot,
  Budget,
  Card,
  Models,
  Reviewer,
  Chat,
  CrewState,
  Profile,
  Pull,
  ReviewInbox,
  ReviewState,
  Room,
  RoomChange,
  RoomMessage,
  Routine,
  RoutineFile,
  Routines,
  StoredEvent,
  Task,
  UsageSummary,
  Plans,
  TaskStatus,
  McpServerInfo,
  InstalledMcp,
  RegistryServer,
  McpCheckResult,
  Skill,
  Draft,
  Submitted,
  Attachment,
} from "./types";
import type { BroughtIn, FoundOut, PageOut, PermissionOut, ProjectOut, ProjectsOut, UpdateOut, VersionOut } from "./api-types";

import { bridge } from "./bridge";

export { bridge };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await bridge.request(method, path, body);
  if (!response.ok) throw new Error(response.data?.detail ?? `request failed (${response.status})`);
  return response.data as T;
}

export const api = {
  bots: () => call<Bot[]>("GET", "/bots"),
  tasks: () => call<Task[]>("GET", "/tasks?limit=1000"),
  events: async (taskId: string) => {
    let all: StoredEvent[] = [];
    for (;;) {
      const after = all.length ? all[all.length - 1].id : 0;
      const batch = await call<StoredEvent[]>("GET", `/tasks/${taskId}/events?after=${after}&limit=5000`);
      all = all.concat(batch);
      if (batch.length < 5000) return all;
    }
  },
  recent: (limit = 400) => call<StoredEvent[]>("GET", `/events/recent?limit=${limit}`),
  review: (taskId: string) => call<ReviewState>("GET", `/tasks/${taskId}/review`),
  changes: (taskId: string) => call<{ diff: string | null }>("GET", `/tasks/${taskId}/changes`),
  newTask: (bot: string, goal: string, workspace?: string) =>
    call<Task>("POST", "/tasks", { bot, goal, workspace: workspace || null }),
  send: (taskId: string, text: string, attachments: string[] = []) => call<Task>("POST", `/tasks/${taskId}/messages`, { text, attachments }),
  cancel: (taskId: string) => call<Task>("POST", `/tasks/${taskId}/cancel`),
  merge: (taskId: string, force = false) => call<Task>("POST", `/tasks/${taskId}/merge`, { force }),
  withdrawMerge: (taskId: string, reason: string) => call<Task>("POST", `/tasks/${taskId}/withdraw-merge`, { reason }),
  release: (taskId: string) => call<Task>("POST", `/tasks/${taskId}/release`),
  answerPermission: (id: number, allowed: boolean) => call<PermissionOut>("POST", `/permissions/${id}`, { allowed }),
  crew: () => call<CrewState>("GET", "/crew"),
  mergeSettings: () => call<{ auto_merge: boolean }>("GET", "/crew/merge-settings"),
  setMergeSettings: (auto_merge: boolean) => call<{ auto_merge: boolean }>("PUT", "/crew/merge-settings", { auto_merge }),
  routines: () => call<Routines>("GET", "/routines"),
  budgets: () => call<Budget[]>("GET", "/budgets"),
  chats: () => call<Chat[]>("GET", "/chats"),
  threadFor: (bot: string, goal: string) => call<Task>("POST", `/chats/bot/${encodeURIComponent(bot)}/threads`, { goal }),
  rememberFor: (bot: string, text: string) => call<{ title: string; path: string }>("POST", `/chats/bot/${encodeURIComponent(bot)}/notes`, { text }),
  /** Pins chats to the top of the sidebar, in this order; the rest are unpinned. */
  setPins: (pins: string[]) => call<{ pins: string[] }>("PUT", "/pins", { pins }),
  say: (bot: string, text: string, attachments: string[] = []) => call<Task>("POST", `/chats/bot/${encodeURIComponent(bot)}/messages`, { text, attachments }),
  read: (key: string) => call<{ ok: boolean }>("POST", `/chats/${encodeURIComponent(key)}/read`),
  roomMessages: (roomId: string) => call<RoomMessage[]>("GET", `/rooms/${roomId}/messages?limit=5000`),
  post: (roomId: string, text: string, attachments: string[] = []) => call<RoomMessage>("POST", `/rooms/${roomId}/messages`, { text, attachments }),
  uploadAttachment: (name: string, data: string) => call<Attachment>("POST", "/attachments", { name, data }),
  attachment: (id: string) => call<{ name: string; mime: string; data: string }>("GET", `/attachments/${id}`),
  newRoom: (title: string, members: string[], lead?: string) => call<Room>("POST", "/rooms", { title, members, lead: lead ?? null }),
  carryOn: (roomId: string) => call<Room>("POST", `/rooms/${roomId}/continue`),
  react: (id: number, reaction: "up" | "down") => call<unknown>("POST", `/proactive/${id}/react`, { reaction }),
  changeRoom: (roomId: string, change: RoomChange) => call<Room>("PATCH", `/rooms/${roomId}`, change),
  deleteRoom: (roomId: string) => call<null>("DELETE", `/rooms/${roomId}`),
  card: (bot: string) => call<Card>("GET", `/bots/${encodeURIComponent(bot)}/card`),
  saveCard: (bot: string, fields: Partial<Card>) => call<Card>("PUT", `/bots/${encodeURIComponent(bot)}/card`, fields),
  newBot: (fields: Partial<Card> & { name: string }) => call<Card>("POST", "/bots", fields),
  routineFile: (name: string) => call<RoutineFile>("GET", `/routines/${encodeURIComponent(name)}/file`),
  saveRoutine: (name: string, fields: Partial<RoutineFile>) => call<RoutineFile>("PUT", `/routines/${encodeURIComponent(name)}/file`, fields),
  newRoutine: (fields: Partial<RoutineFile> & { name: string }) => call<RoutineFile>("POST", "/routines", fields),
  deleteRoutine: (name: string) => call<{ ok: boolean }>("DELETE", `/routines/${encodeURIComponent(name)}`),
  pause: (on: boolean) => call<CrewState>("POST", on ? "/crew/pause" : "/crew/resume"),
  version: () => call<VersionOut>("GET", "/version"),
  update: (fetch = false) => call<UpdateOut>("GET", `/update?app=${__APP_COMMIT__}${fetch ? "&fetch=true" : ""}`),
  startUpdate: () => call<UpdateOut>("POST", "/update", { app: __APP_COMMIT__ || null }),
  clientError: (error: { where: string; message: string; stack: string | null }) => call<void>("POST", "/client-errors", error),
  /** Restarts the daemon once no turn is running (or now, `whenIdle` false); turns cut off carry on after. */
  restart: (whenIdle: boolean) => call<CrewState>("POST", "/crew/restart", { when_idle: whenIdle }),
  cancelRestart: () => call<CrewState>("DELETE", "/crew/restart"),
  usageSummary: (days = 365) => call<UsageSummary>("GET", `/usage/summary?days=${days}`),
  mcpServers: () => call<McpServerInfo[]>("GET", "/mcp/servers"),
  saveMcpServer: (name: string, body: { transport: "stdio" | "http" | "sse"; command?: string; args?: string[]; env?: Record<string, string> | null; url?: string; headers?: Record<string, string> | null }) =>
    call<McpServerInfo>("PUT", `/mcp/servers/${encodeURIComponent(name)}`, body),
  removeMcpServer: (name: string) => call<{ removed: string; bots: string[] }>("DELETE", `/mcp/servers/${encodeURIComponent(name)}`),
  checkMcpServer: (name: string) => call<McpCheckResult>("POST", `/mcp/servers/${encodeURIComponent(name)}/check`),
  installedMcp: () => call<InstalledMcp[]>("GET", "/mcp/installed"),
  importMcp: (id: string, name?: string) => call<McpServerInfo>("POST", "/mcp/import", { id, name }),
  searchMcp: (q: string) => call<RegistryServer[]>("GET", `/mcp/registry?q=${encodeURIComponent(q)}`),
  addFromRegistry: (body: { server: string; version: string; option: string; name: string; values: Record<string, string> }) =>
    call<McpServerInfo>("POST", "/mcp/registry", body),
  skills: () => call<Skill[]>("GET", "/skills"),
  drafts: (repo: string, number: number) => call<Draft[]>("GET", `/reviews/${repo}/${number}/drafts`),
  addDraft: (repo: string, number: number, body: { path: string; line: number; side: "RIGHT" | "LEFT"; body: string }) =>
    call<Draft>("POST", `/reviews/${repo}/${number}/drafts`, body),
  editDraft: (id: number, body: string) => call<Draft>("PUT", `/reviews/drafts/${id}`, { body }),
  deleteDraft: (id: number) => call<{ deleted: number }>("DELETE", `/reviews/drafts/${id}`),
  submitReview: (repo: string, number: number, body: { event: "APPROVE" | "REQUEST_CHANGES" | "COMMENT"; body: string }) =>
    call<Submitted>("POST", `/reviews/${repo}/${number}/submit`, body),
  pullImage: (repo: string, number: number, path: string, side: "head" | "base") =>
    call<{ mime: string; data: string }>("GET", `/reviews/${repo}/${number}/image?path=${encodeURIComponent(path)}&side=${side}`),
  taskStatus: (id: string) => call<TaskStatus>("GET", `/tasks/${id}/status`),
  plans: (fresh = false) => call<Plans>("GET", `/plans${fresh ? "?fresh=true" : ""}`),
  // Kept answers come back at once; `fresh` waits for GitHub. Your teams' list is asked for apart, when it's opened.
  reviewInbox: (fresh = false, lists = ["assigned", "direct", "mine"]) =>
    call<ReviewInbox>("GET", `/reviews/inbox?lists=${lists.join(",")}${fresh ? "&fresh=true" : ""}`),
  pull: (repo: string, number: number) => call<Pull>("GET", `/reviews/${repo}/${number}`),
  pullDiff: (repo: string, number: number) => call<{ diff: string | null }>("GET", `/reviews/${repo}/${number}/diff`),
  startReview: (repo: string, number: number, note = "") => call<Task[]>("POST", `/reviews/${repo}/${number}/review`, { note }),
  reviewers: () => call<Reviewer[]>("GET", "/reviews/reviewers"),
  setReviewers: (reviewers: Reviewer[]) => call<Reviewer[]>("PUT", "/reviews/reviewers", reviewers),
  models: (fresh = false) => call<Models>("GET", `/models${fresh ? "?fresh=true" : ""}`),
  reviewRepos: () => call<Record<string, string>>("GET", "/reviews/repos"),
  setReviewRepo: (repo: string, folder: string | null) => call<Record<string, string>>("PUT", `/reviews/repos/${repo}`, { folder }),
  profile: (bot: string) => call<Profile>("GET", `/bots/${encodeURIComponent(bot)}/profile`),
  addStarter: (name: string) => call<Routine>("POST", `/routines/starters/${encodeURIComponent(name)}`),
  runRoutine: (name: string) => call<Task>("POST", `/routines/${encodeURIComponent(name)}/run`),
  confirmRoutine: (name: string) => call<Task | null>("POST", `/routines/${encodeURIComponent(name)}/confirm`),
  projects: () => call<ProjectsOut>("GET", "/projects"),
  saveProject: (name: string, folder: string, about: string) =>
    call<ProjectOut>("PUT", `/projects/${encodeURIComponent(name)}`, { folder, about }),
  removeProject: (name: string) => call<null>("DELETE", `/projects/${encodeURIComponent(name)}`),
  /** The folders the user worked in lately with Claude Code or Codex, and the memories those keep. */
  foundProjects: () => call<FoundOut>("GET", "/projects/found"),
  bringIn: (projects: { name: string; folder: string; about: string }[], memories: string[]) =>
    call<BroughtIn>("POST", "/projects/bring-in", { projects, memories }),
  /** The user started the crew off, or said not now: the offer isn't made again. */
  started: () => call<null>("POST", "/projects/started"),
  pages: (bot: string) => call<PageOut[]>("GET", `/bots/${encodeURIComponent(bot)}/pages`),
  page: (bot: string, name: string) => call<PageOut>("GET", `/bots/${encodeURIComponent(bot)}/pages/${pagePath(name)}`),
  savePage: (bot: string, name: string, text: string) =>
    call<PageOut>("PUT", `/bots/${encodeURIComponent(bot)}/pages/${pagePath(name)}`, { text }),
};

/** A page's name in a URL: each part encoded, its folders kept. */
function pagePath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}
