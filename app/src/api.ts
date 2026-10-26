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
  RoomMessage,
  Routine,
  RoutineFile,
  Routines,
  StoredEvent,
  Task,
  UsageSummary,
  Plans,
} from "./types";

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
  send: (taskId: string, text: string) => call<Task>("POST", `/tasks/${taskId}/messages`, { text }),
  cancel: (taskId: string) => call<Task>("POST", `/tasks/${taskId}/cancel`),
  merge: (taskId: string, force = false) => call<Task>("POST", `/tasks/${taskId}/merge`, { force }),
  release: (taskId: string) => call<Task>("POST", `/tasks/${taskId}/release`),
  crew: () => call<CrewState>("GET", "/crew"),
  routines: () => call<Routines>("GET", "/routines"),
  budgets: () => call<Budget[]>("GET", "/budgets"),
  chats: () => call<Chat[]>("GET", "/chats"),
  say: (bot: string, text: string) => call<Task>("POST", `/chats/bot/${encodeURIComponent(bot)}/messages`, { text }),
  read: (key: string) => call<{ ok: boolean }>("POST", `/chats/${encodeURIComponent(key)}/read`),
  roomMessages: (roomId: string) => call<RoomMessage[]>("GET", `/rooms/${roomId}/messages?limit=5000`),
  post: (roomId: string, text: string) => call<RoomMessage>("POST", `/rooms/${roomId}/messages`, { text }),
  newRoom: (title: string, members: string[], lead?: string) => call<Room>("POST", "/rooms", { title, members, lead: lead ?? null }),
  card: (bot: string) => call<Card>("GET", `/bots/${encodeURIComponent(bot)}/card`),
  saveCard: (bot: string, fields: Partial<Card>) => call<Card>("PUT", `/bots/${encodeURIComponent(bot)}/card`, fields),
  newBot: (fields: Partial<Card> & { name: string }) => call<Card>("POST", "/bots", fields),
  routineFile: (name: string) => call<RoutineFile>("GET", `/routines/${encodeURIComponent(name)}/file`),
  saveRoutine: (name: string, fields: Partial<RoutineFile>) => call<RoutineFile>("PUT", `/routines/${encodeURIComponent(name)}/file`, fields),
  newRoutine: (fields: Partial<RoutineFile> & { name: string }) => call<RoutineFile>("POST", "/routines", fields),
  deleteRoutine: (name: string) => call<{ ok: boolean }>("DELETE", `/routines/${encodeURIComponent(name)}`),
  pause: (on: boolean) => call<CrewState>("POST", on ? "/crew/pause" : "/crew/resume"),
  usageSummary: (days = 371) => call<UsageSummary>("GET", `/usage/summary?days=${days}`),
  plans: (fresh = false) => call<Plans>("GET", `/plans${fresh ? "?fresh=true" : ""}`),
  reviewInbox: (fresh = false) => call<ReviewInbox>("GET", `/reviews/inbox${fresh ? "?fresh=true" : ""}`),
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
};
