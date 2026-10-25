import type { Bot, ConnectionStatus, Live, ReviewState, StoredEvent, Task } from "./types";

// What electron/preload.cjs exposes. The token stays in the main process.
interface Bridge {
  status(): Promise<ConnectionStatus>;
  request(method: string, path: string, body?: unknown): Promise<{ ok: boolean; status: number; data: any }>;
  pickFolder(): Promise<string | null>;
  reveal(path: string): Promise<void>;
  onStatus(callback: (status: ConnectionStatus) => void): () => void;
  onLive(callback: (message: Live) => void): () => void;
  onResync(callback: () => void): () => void;
}

declare global {
  interface Window {
    crew: Bridge;
  }
}

export const bridge = window.crew;

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
  review: (taskId: string) => call<ReviewState>("GET", `/tasks/${taskId}/review`),
  changes: (taskId: string) => call<{ diff: string | null }>("GET", `/tasks/${taskId}/changes`),
  newTask: (bot: string, goal: string, workspace?: string) =>
    call<Task>("POST", "/tasks", { bot, goal, workspace: workspace || null }),
  send: (taskId: string, text: string) => call<Task>("POST", `/tasks/${taskId}/messages`, { text }),
  cancel: (taskId: string) => call<Task>("POST", `/tasks/${taskId}/cancel`),
  merge: (taskId: string, force = false) => call<Task>("POST", `/tasks/${taskId}/merge`, { force }),
};
