import type { Bot, Budget, CrewState, ReviewState, Routine, Routines, StoredEvent, Task } from "./types";

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
  addStarter: (name: string) => call<Routine>("POST", `/routines/starters/${encodeURIComponent(name)}`),
  runRoutine: (name: string) => call<Task>("POST", `/routines/${encodeURIComponent(name)}/run`),
};
