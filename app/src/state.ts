import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, bridge } from "./api";
import { setCrew } from "./components/Avatar";
import type { Bot, Chat, ConnectionStatus, CrewState, Live, ReviewInbox, RoomMessage, Routines, StoredEvent, Task } from "./types";
import { ACTIVE } from "./types";

const RECENT = 400;
const ROUTINES_EVERY_MS = 60_000; // next and last run times move on their own
const REVIEWS_EVERY_MS = 5 * 60_000; // pull requests waiting on you, from GitHub

/** Everything a layout needs: the crew, its tasks, recent activity, and the actions. Layouts only arrange it. */
export function useCrew() {
  const [status, setStatus] = useState<ConnectionStatus>({ state: "connecting", home: "" });
  const [bots, setBots] = useState<Bot[]>([]);
  const [tasks, setTasks] = useState<Map<string, Task>>(new Map());
  const [events, setEvents] = useState<Map<string, StoredEvent[]>>(new Map()); // only tasks someone opened
  const [recent, setRecent] = useState<StoredEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [crewState, setCrewState] = useState<CrewState | null>(null);
  const [routines, setRoutines] = useState<Routines | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [roomMessages, setRoomMessages] = useState<Map<string, RoomMessage[]>>(new Map()); // only rooms someone opened

  // The chat list's previews and unread counts come from the daemon; a burst of live updates reloads it once.
  const chatsTimer = useRef<number | undefined>(undefined);
  const loadChats = useCallback(() => api.chats().then(setChats, () => undefined), []);
  const reloadChats = useCallback(() => {
    window.clearTimeout(chatsTimer.current);
    chatsTimer.current = window.setTimeout(loadChats, 250);
  }, [loadChats]);

  const [reviewInbox, setReviewInbox] = useState<ReviewInbox | null>(null);
  const loadReviews = useCallback((fresh = false) => api.reviewInbox(fresh).then(setReviewInbox, () => undefined), []);

  const loadRoutines = useCallback(() => api.routines().then(setRoutines, () => setRoutines(null)), []);

  const loadAll = useCallback(async () => {
    try {
      const [b, t, r, c] = await Promise.all([api.bots(), api.tasks(), api.recent(RECENT), api.crew()]);
      setCrewState(c);
      loadRoutines();
      loadChats();
      setBots(b);
      setCrew(b);
      setTasks(new Map(t.map((task) => [task.id, task])));
      setRecent(r as StoredEvent[]);
      setEvents(new Map());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [loadRoutines, loadChats]);

  useEffect(() => {
    bridge.status().then(setStatus);
    const offs = [
      bridge.onStatus(setStatus),
      bridge.onResync(loadAll),
      bridge.onLive((message: Live) => {
        if (message.type === "task") {
          setTasks((current) => new Map(current).set(message.task.id, message.task));
          reloadChats();
          return;
        }
        if (message.type === "crew") {
          setCrewState(message.crew);
          return;
        }
        if (message.type === "room") {
          reloadChats();
          return;
        }
        if (message.type === "room_message") {
          const m = message.message;
          setRoomMessages((current) => {
            const known = current.get(m.room_id);
            if (!known || known.some((x) => x.id === m.id)) return current;
            return new Map(current).set(m.room_id, [...known, m]);
          });
          reloadChats();
          return;
        }
        reloadChats();
        const event = message.event;
        setRecent((current) => (current.some((e) => e.id === event.id) ? current : [...current.slice(-RECENT + 1), event]));
        setEvents((current) => {
          const known = current.get(event.task_id);
          if (!known || known.some((e) => e.id === event.id)) return current;
          return new Map(current).set(event.task_id, [...known, event]);
        });
      }),
    ];
    return () => offs.forEach((off) => off());
  }, [loadAll, reloadChats]);

  useEffect(() => {
    if (status.state === "connected") loadAll();
  }, [status.state, loadAll]);

  useEffect(() => {
    if (status.state !== "connected") return;
    loadReviews();
    const timers = [setInterval(loadRoutines, ROUTINES_EVERY_MS), setInterval(loadReviews, REVIEWS_EVERY_MS)];
    return () => timers.forEach(clearInterval);
  }, [status.state, loadRoutines, loadReviews]);

  const ensureEvents = useCallback(
    (taskId: string) => {
      if (events.has(taskId)) return;
      setEvents((current) => new Map(current).set(taskId, [])); // claim it, so it loads once
      api.events(taskId).then(
        (loaded) => setEvents((current) => new Map(current).set(taskId, loaded as StoredEvent[])),
        (e) => setError(String(e)),
      );
    },
    [events],
  );

  const ensureRoom = useCallback(
    (roomId: string) => {
      if (roomMessages.has(roomId)) return;
      setRoomMessages((current) => new Map(current).set(roomId, []));
      api.roomMessages(roomId).then(
        (loaded) => setRoomMessages((current) => new Map(current).set(roomId, loaded)),
        (e) => setError(String(e)),
      );
    },
    [roomMessages],
  );

  const act = useCallback(
    <T,>(promise: Promise<T>) =>
      promise.catch((e) => {
        setError(String(e).replace(/^Error: /, ""));
        throw e;
      }),
    [],
  );

  const derived = useMemo(() => derive(tasks, recent), [tasks, recent]);

  return {
    status,
    crewState,
    routines,
    chats,
    roomMessages,
    ensureRoom,
    say: (bot: string, text: string) => act(api.say(bot, text)),
    post: (roomId: string, text: string) => act(api.post(roomId, text)),
    read: (key: string) => api.read(key).then(reloadChats, () => undefined),
    newRoom: async (title: string, members: string[], lead?: string) => {
      const room = await act(api.newRoom(title, members, lead));
      loadChats();
      return room;
    },
    bots,
    tasks,
    events,
    recent,
    error,
    clearError: () => setError(null),
    ensureEvents,
    ...derived,
    reviewInbox,
    loadReviews,
    pause: (on: boolean) => act(api.pause(on)).then(setCrewState),
    /** Reads the crew again after an edit: its bots, their chats and the routines. */
    refresh: async () => {
      const b = await api.bots();
      setCrew(b);
      setBots(b);
      loadChats();
      loadRoutines();
    },
    newTask: (bot: string, goal: string, workspace?: string) => act(api.newTask(bot, goal, workspace)),
    send: (taskId: string, text: string) => act(api.send(taskId, text)),
    cancel: (taskId: string) => act(api.cancel(taskId)),
    merge: (taskId: string, force: boolean) => act(api.merge(taskId, force)),
    release: (taskId: string) => act(api.release(taskId)),
    addStarter: async (name: string) => {
      await act(api.addStarter(name));
      loadRoutines();
    },
    runRoutine: async (name: string) => {
      const task = await act(api.runRoutine(name));
      loadRoutines();
      return task;
    },
  };
}

export type Crew = ReturnType<typeof useCrew>;

export interface InboxItem {
  task: Task;
  reason: "merge" | "question" | "held" | "failed" | "interrupted";
  text: string;
}

function derive(tasks: Map<string, Task>, recent: StoredEvent[]) {
  const all = [...tasks.values()];
  const byAge = [...all].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const children = new Map<string, Task[]>();
  for (const t of byAge) if (t.parent_id) children.set(t.parent_id, [...(children.get(t.parent_id) ?? []), t]);
  const goals = byAge.filter((t) => !t.parent_id || !tasks.has(t.parent_id)).reverse();

  const latest = new Map<string, StoredEvent>(); // the last thing each task did, for one-line status
  const verdicts = new Map<string, StoredEvent[]>(); // review events, by the work they reviewed
  for (const e of recent) {
    if (["text", "tool_call", "handoff", "review", "escalation", "merged", "merge_proposed"].includes(e.kind)) {
      latest.set(e.task_id, e);
    }
    if (e.kind === "review" && e.data.task_id === e.task_id) {
      verdicts.set(e.data.work_id, [...(verdicts.get(e.data.work_id) ?? []), e]);
    }
  }

  const questions = new Map<string, string>();
  for (const e of recent) if (e.kind === "escalation") questions.set(e.task_id, e.data.question);
  const holds = new Map<string, string>();
  for (const e of recent) if (e.kind === "held") holds.set(e.task_id, e.data.reason);

  const inbox: InboxItem[] = [];
  for (const task of [...all].sort((a, b) => b.updated_at.localeCompare(a.updated_at))) {
    if (task.merge_state === "proposed") {
      inbox.push({ task, reason: "merge", text: "Reviewed and ready to merge" });
    } else if (task.status === "needs_input" && !task.parent_id) {
      inbox.push({ task, reason: "question", text: questions.get(task.id) ?? "Waiting on your answer" });
    } else if (task.status === "held") {
      inbox.push({ task, reason: "held", text: holds.get(task.id) ?? "Waiting on a budget" });
    } else if (task.status === "failed" && !task.parent_id) {
      inbox.push({ task, reason: "failed", text: task.error ?? "Failed" });
    } else if (task.status === "interrupted" && !task.parent_id) {
      inbox.push({ task, reason: "interrupted", text: "Stopped when the daemon did; send a message to resume" });
    }
  }

  const family = (id: string): Task[] => (children.get(id) ?? []).flatMap((k) => [k, ...family(k.id)]);
  const running = all.filter((t) => ACTIVE.includes(t.status));
  return { goals, children, family, latest, verdicts, inbox, running, holds };
}

/** One human line for what an event says, or null when it is noise for a feed. */
export function describe(event: StoredEvent, tasks: Map<string, Task>): { text: string; tone?: string } | null {
  const task = tasks.get(event.task_id);
  const bot = task?.bot ?? "a bot";
  const d = event.data;
  switch (event.kind) {
    case "message":
      if (d.from === "crewbots") return null;
      return { text: d.from === "user" ? `You → ${bot}: ${oneLine(d.text)}` : `${d.from} → ${bot}: ${oneLine(d.text)}` };
    case "handoff":
      return { text: `${bot} handed off: ${oneLine(d.done)}` };
    case "review":
      if (d.task_id !== event.task_id) return null; // the copy recorded on the work
      return {
        text: `${d.reviewer} ${d.verdict === "pass" ? "passed" : "failed"} ${oneLine(tasks.get(d.work_id)?.title ?? d.work_id)}`,
        tone: d.verdict === "pass" ? "good" : "bad",
      };
    case "escalation":
      return { text: `${bot} asks: ${oneLine(d.question)}`, tone: "warn" };
    case "merge_proposed":
      return { text: `${d.by} proposed ${oneLine(task?.title ?? "work")} for merging`, tone: "accent" };
    case "merged":
      return { text: `Merged ${oneLine(task?.title ?? "")}`, tone: "good" };
    case "held":
      return { text: `${bot} is waiting: ${oneLine(d.reason)}`, tone: "warn" };
    case "rate_limit":
      return d.status === "ok" ? null : { text: limitText(d as Parameters<typeof limitText>[0]), tone: d.status === "limited" ? "bad" : "warn" };
    case "done":
      return d.is_error ? { text: `${bot} failed: ${oneLine(d.error ?? "")}`, tone: "bad" } : null;
    default:
      return null;
  }
}

/** What a task is doing right now, in a few words: "Running npm test", "Editing ExportButton.tsx". */
export function doing(event: StoredEvent | undefined): string {
  if (!event) return "";
  const d = event.data;
  switch (event.kind) {
    case "text":
      return oneLine(d.text);
    case "tool_call":
      return describeCall(String(d.name), d.input ?? {});
    case "handoff":
      return `Handed back: ${oneLine(d.done)}`;
    case "review":
      return `${d.verdict === "pass" ? "Passed" : "Requested changes"}: ${oneLine(d.summary)}`;
    case "escalation":
      return `Asks: ${oneLine(d.question)}`;
    case "merge_proposed":
      return "Proposed for merging";
    case "merged":
      return "Merged";
    default:
      return "";
  }
}

/** "Claude plan: 82% of the five-hour limit used" */
export function limitText(limit: { plan: string; window: string; status: string; used_percent?: number | null; resets_at?: number | null }): string {
  const plan = limit.plan.charAt(0).toUpperCase() + limit.plan.slice(1);
  const window = WINDOWS[limit.window] ?? limit.window.replace(/_/g, " ");
  const resets = limit.resets_at ? `, resets ${new Date(limit.resets_at * 1000).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}` : "";
  if (limit.status === "limited") return `${plan} plan: the ${window} limit is reached${resets}`;
  const used = limit.used_percent != null ? `${Math.round(limit.used_percent)}% of` : "close to";
  return `${plan} plan: ${used} the ${window} limit used${resets}`;
}

const WINDOWS: Record<string, string> = {
  five_hour: "five-hour",
  seven_day: "weekly",
  seven_day_opus: "weekly Opus",
  seven_day_sonnet: "weekly Sonnet",
  "5h": "five-hour",
  "7d": "weekly",
};

export function describeCall(name: string, input: Record<string, any>): string {
  const file = (path: unknown) => String(path ?? "").split("/").filter(Boolean).at(-1) ?? "";
  switch (name.replace("mcp__crewbots__", "")) {
    case "Bash":
    case "shell":
      return `Running ${oneLine(unwrapShell(String(input.command ?? "")), 90)}`;
    case "Read":
      return `Reading ${file(input.file_path)}`;
    case "Grep":
      return `Searching for ${oneLine(String(input.pattern ?? ""), 60)}`;
    case "Glob":
      return `Looking for ${oneLine(String(input.pattern ?? ""), 60)}`;
    case "Edit":
    case "Write":
    case "MultiEdit":
      return `Editing ${file(input.file_path)}`;
    case "apply_patch":
      return `Editing ${(input.changes ?? []).map((c: any) => file(c.path)).join(", ")}`;
    case "WebSearch":
    case "web_search":
      return `Searching the web for ${oneLine(String(input.query ?? ""), 60)}`;
    case "WebFetch":
      return `Reading ${oneLine(String(input.url ?? ""), 60)}`;
    case "delegate":
      return `Handing work to ${input.bot}`;
    case "tasks":
      return "Checking on the crew";
    default:
      return name.replace(/^mcp__\w+?__/, "").replace(/_/g, " ");
  }
}

/** Codex wraps commands as /bin/zsh -lc '…'; show the command itself. */
function unwrapShell(command: string): string {
  const match = /^\/bin\/(?:ba|z)?sh -lc (['"])([\s\S]*)\1$/.exec(command.trim());
  return match ? match[2] : command;
}

export function oneLine(text: string, max = 140): string {
  const flat = String(text ?? "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // [file.py](/long/path) reads as file.py
    .replace(/\*\*|`/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return flat.length > max ? flat.slice(0, max - 1) + "…" : flat;
}
