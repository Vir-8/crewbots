import { useCallback, useEffect, useMemo, useState } from "react";
import { api, bridge } from "./api";
import type { Bot, ConnectionStatus, Live, StoredEvent, Task } from "./types";
import { ACTIVE } from "./types";

const RECENT = 400;

/** Everything a layout needs: the crew, its tasks, recent activity, and the actions. Layouts only arrange it. */
export function useCrew() {
  const [status, setStatus] = useState<ConnectionStatus>({ state: "connecting", home: "" });
  const [bots, setBots] = useState<Bot[]>([]);
  const [tasks, setTasks] = useState<Map<string, Task>>(new Map());
  const [events, setEvents] = useState<Map<string, StoredEvent[]>>(new Map()); // only tasks someone opened
  const [recent, setRecent] = useState<StoredEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    try {
      const [b, t, r] = await Promise.all([api.bots(), api.tasks(), api.recent(RECENT)]);
      setBots(b);
      setTasks(new Map(t.map((task) => [task.id, task])));
      setRecent(r as StoredEvent[]);
      setEvents(new Map());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    bridge.status().then(setStatus);
    const offs = [
      bridge.onStatus(setStatus),
      bridge.onResync(loadAll),
      bridge.onLive((message: Live) => {
        if (message.type === "task") {
          setTasks((current) => new Map(current).set(message.task.id, message.task));
          return;
        }
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
  }, [loadAll]);

  useEffect(() => {
    if (status.state === "connected") loadAll();
  }, [status.state, loadAll]);

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
    bots,
    tasks,
    events,
    recent,
    error,
    clearError: () => setError(null),
    ensureEvents,
    ...derived,
    newTask: (bot: string, goal: string, workspace?: string) => act(api.newTask(bot, goal, workspace)),
    send: (taskId: string, text: string) => act(api.send(taskId, text)),
    cancel: (taskId: string) => act(api.cancel(taskId)),
    merge: (taskId: string, force: boolean) => act(api.merge(taskId, force)),
  };
}

export type Crew = ReturnType<typeof useCrew>;

export interface InboxItem {
  task: Task;
  reason: "merge" | "question" | "failed" | "interrupted";
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

  const inbox: InboxItem[] = [];
  for (const task of [...all].sort((a, b) => b.updated_at.localeCompare(a.updated_at))) {
    if (task.merge_state === "proposed") {
      inbox.push({ task, reason: "merge", text: "Reviewed and ready to merge" });
    } else if (task.status === "needs_input" && !task.parent_id) {
      inbox.push({ task, reason: "question", text: questions.get(task.id) ?? "Waiting on your answer" });
    } else if (task.status === "failed" && !task.parent_id) {
      inbox.push({ task, reason: "failed", text: task.error ?? "Failed" });
    } else if (task.status === "interrupted" && !task.parent_id) {
      inbox.push({ task, reason: "interrupted", text: "Stopped when the daemon did; send a message to resume" });
    }
  }

  const family = (id: string): Task[] => (children.get(id) ?? []).flatMap((k) => [k, ...family(k.id)]);
  const running = all.filter((t) => ACTIVE.includes(t.status));
  return { goals, children, family, latest, verdicts, inbox, running };
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
