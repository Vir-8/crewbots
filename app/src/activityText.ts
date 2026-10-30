import type { StoredEvent, Task } from "./types";

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

/** The working line under a conversation: what `doing` says, but never a text, which the conversation shows as a
 * message just above it. */
export function workingLine(event: StoredEvent | undefined): string {
  return event?.kind === "text" ? "" : doing(event);
}

/** "Claude plan: 82% of the five-hour limit used" */
export function limitText(limit: { plan: string; window: string; status: string; used_percent?: number | null; resets_at?: number | null }): string {
  const plan = limit.plan.charAt(0).toUpperCase() + limit.plan.slice(1);
  const window = WINDOWS[limit.window] ?? limit.window.replace(/_/g, " ");
  const when = limit.resets_at ? `, resets ${resets(limit.resets_at)}` : "";
  if (limit.status === "limited") return `${plan} plan: the ${window} limit is reached${when}`;
  const used = limit.used_percent != null ? `${Math.round(limit.used_percent)}% of` : "close to";
  return `${plan} plan: ${used} the ${window} limit used${when}`;
}

/** When a limit resets, from now: "at 5:40 pm", "tomorrow at 9:00 am", "Wed, 28 Oct at 11:30 pm". */
export function resets(unix: number): string {
  const d = new Date(unix * 1000);
  const days = Math.round((new Date(d.toDateString()).getTime() - new Date(new Date().toDateString()).getTime()) / 86_400_000);
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return days === 0 ? `at ${time}` : days === 1 ? `tomorrow at ${time}` : `${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} at ${time}`;
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
      return `Running ${plain(unwrapShell(String(input.command ?? "")), 90)}`;
    case "Read":
      return `Reading ${file(input.file_path)}`;
    case "Grep":
      return `Searching for ${pattern(input.pattern)}${input.path ? ` in ${file(input.path)}` : ""}`;
    case "Glob":
      return `Looking for ${plain(String(input.pattern ?? ""), 60)}${input.path ? ` in ${file(input.path)}` : ""}`;
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
      return `Reading ${plain(String(input.url ?? ""), 60)}`;
    case "delegate":
      return `Handing work to ${input.bot}`;
    case "share":
      return `Sharing ${(input.files ?? []).map(file).join(", ")}`;
    case "view_image":
      return `Looking at ${file(input.path)}`;
    case "image_generation":
      return `Making an image${input.prompt ? `: ${oneLine(String(input.prompt), 60)}` : ""}`;
    case "tasks":
      return "Checking on the crew";
    default:
      return name.replace(/^mcp__\w+?__/, "").replace(/_/g, " ");
  }
}

/** A search's pattern as written, but "(?i)", the regular-expression flag for ignoring case, said in words. */
function pattern(text: unknown): string {
  const written = String(text ?? "");
  const anyCase = written.startsWith("(?i)");
  return plain(anyCase ? written.slice(4) : written, 60) + (anyCase ? " (any case)" : "");
}

/** A command, pattern or address on one line, as it was written: unlike prose, its `**` and backticks are part of it. */
function plain(text: string, max: number): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > max ? flat.slice(0, max - 1) + "…" : flat;
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
