// A conversation's events as people would tell them: what was said and decided, as blocks, with the mechanics
// folded away (Thread draws them).

import { limitText, oneLine } from "../state";
import type { Attachment, StoredEvent, Task } from "../types";
import { tokens } from "./bits";
import { shown } from "./Find";

export type Choice = { label: string; description?: string };

export type Block =
  | { kind: "you"; id: number; at: string; text: string; files: Attachment[] }
  | { kind: "brief"; id: number; at: string; from: string; text: string }
  | { kind: "say"; id: number; at: string; text: string; tag?: string; raised?: number; files?: Attachment[] }
  | { kind: "steps"; id: number; calls: { call: StoredEvent; result?: StoredEvent }[] }
  | { kind: "work"; id: string; task: Task }
  | { kind: "asked"; id: number; bot: string; question: string; task?: Task }
  | { kind: "line"; id: number; text: string; icon?: "check" | "merge" | "branch" | "alert" | "play" | "message" | "repeat"; tone?: string; gist?: string; wake?: boolean } // gist: what it was, in a folded run's few words; wake: what started a turn the crew began
  | { kind: "page"; id: number; page: string; changed: boolean }
  | { kind: "activity"; id: number | string; items: Block[] }
  | { kind: "offer"; id: number; outcome?: Record<string, any> } // the chief's, under its greeting: how it was answered
  // What a bot asks the user to allow: in its own thread, and in the chat its work came from (`here` false there).
  | { kind: "permission"; id: number; permission: number; bot: string; for?: string; taskId: string; ask: string; what: string; why: string; folder?: string; here: boolean; allowed?: boolean }
  | { kind: "ask"; id: number; taskId: string; question: string; choices?: Choice[]; multiple?: boolean; answer?: string; room?: string; closed?: boolean; questionId?: number }
  | { kind: "held"; id: number; taskId: string; reason: string }
  | { kind: "handoff"; id: number; data: Record<string, any> }
  | { kind: "verdict"; id: number; data: Record<string, any> }
  | { kind: "push"; id: number; data: Record<string, any>; started?: boolean; result?: Record<string, any>; replaced?: boolean }
  | { kind: "replace"; id: number; data: Record<string, any>; started?: boolean; result?: Record<string, any> };

// Crew tools that are shown as what they did (a card, a verdict, a question, a note kept, a page), not as steps.
const SHOWN_ELSEWHERE = new Set(
  ["delegate", "start_thread", "post", "share", "handoff", "review", "escalate", "propose_merge", "propose_push", "propose_replace_main", "withdraw_merge", "message", "ask", "remember",
    "forget", "update_state", "write_page", "edit_page", "next_check", "set_name"].map((t) => `mcp__crewbots__${t}`),
);
const COMPACTED = "compacted its conversation"; // the gist of the lines about a conversation grown long
const WOKEN = "woken by the crew";
const NOTE = /^(Remembered|You gave it a note)/;

/** What a block says, as it's drawn: where ⌘F looks. */
export function said(block: Block): string {
  switch (block.kind) {
    case "you":
    case "brief":
    case "line":
      return block.text;
    case "say":
      return shown(block.text);
    case "ask":
      return [shown(block.question), ...(block.choices ?? []).flatMap((c) => [c.label, c.description ?? ""])].join("\n");
    case "asked":
      return block.question;
    case "handoff":
      return [block.data.done, block.data.open ? `Open: ${block.data.open}` : ""].join("\n");
    case "verdict":
      return block.data.summary ?? "";
    case "push":
      return [block.data.project, block.data.summary, block.data.branch, block.data.upstream, block.data.push_url_display, ...(block.data.commits ?? []), block.result?.reason].filter(Boolean).join("\n");
    case "replace":
      return [block.data.project, block.data.summary, block.data.target, block.data.branch, block.data.backup, ...(block.data.checks ?? []).map((c: any) => `${c.name} ${c.detail}`), block.result?.reason].filter(Boolean).join("\n");
    case "page":
      return block.page;
    case "permission":
      return [shown(block.what), shown(block.why), (block.folder ?? "").replace(/^\/Users\/[^/]+/, "~")].filter(Boolean).join("\n");
    case "activity":
      return block.items.map(said).filter(Boolean).join("\n");
    default:
      return "";
  }
}

/** Whether a block is part of a bot's working rather than the conversation: folded away in a chat. What went wrong,
 * and anything that needs the user (a card, a question, a proposal), stays in view. */
export function quiet(block: Block): boolean {
  return block.kind === "steps" || block.kind === "page" || (block.kind === "line" && !block.tone && block.icon !== "merge");
}

/** Runs of a bot's working (two or more quiet blocks) folded into one. */
export function fold(blocks: Block[]): Block[] {
  const out: Block[] = [];
  let run: Block[] = [];
  const close = () => {
    if (run.length > 1 || run.some((b) => b.kind === "line" && b.wake)) out.push({ kind: "activity", id: run[0].id, items: run });
    else out.push(...run);
    run = [];
  };
  for (const block of blocks) {
    if (quiet(block)) run.push(block);
    else {
      close();
      out.push(block);
    }
  }
  close();
  return out;
}

export function build(events: StoredEvent[], children: Task[], tasks: Map<string, Task>): Block[] {
  const out: Block[] = [];
  const delegated = children.filter((c) => !c.review_of && c.kind !== "review" && c.kind !== "ask");
  const asked = new Map(children.filter((c) => c.kind === "ask").map((task) => [task.id, task]));
  const results = new Map(events.filter((e) => e.kind === "tool_result").map((e) => [e.data.id, e]));
  // A successful tool result names the actual child. Other children (for example /new or autonomous work),
  // and calls whose results are missing, are placed at their creation time rather than at the end of the chat.
  const byId = new Map(delegated.map((task) => [task.id, task]));
  const workCalls = new Map<number, Task>();
  const matched = new Set<string>();
  for (const e of events) {
    if (e.kind !== "tool_call" || !["mcp__crewbots__delegate", "mcp__crewbots__start_thread"].includes(e.data.name) || e.data.input?.review_of) continue;
    const result = results.get(e.data.id);
    const id = !result?.data.is_error && String(result?.data.output ?? "").match(/\b(?:Delegated|Opened thread) (t_[a-f0-9]{10})\b/)?.[1];
    const task = id && byId.get(id);
    if (task && !matched.has(task.id)) {
      workCalls.set(e.id, task);
      matched.add(task.id);
    }
  }
  const unanchored = delegated.filter((task) => !matched.has(task.id)).sort((a, b) => a.created_at.localeCompare(b.created_at));
  // A try that failed for a passing reason, and a later try of the same turn got past: no longer an error to show.
  const recovered = new Set<number>();
  let failed: number[] = [];
  for (const e of events) {
    if (e.kind === "turn_start") failed = [];
    else if (e.kind === "done" && e.data.is_error && e.data.retry) failed.push(e.id);
    else if (e.kind === "done" && !e.data.is_error) failed.splice(0).forEach((id) => recovered.add(id));
  }
  let steps: { call: StoredEvent; result?: StoredEvent }[] = [];
  const flush = () => {
    if (steps.length) out.push({ kind: "steps", id: steps[0].call.id, calls: steps });
    steps = [];
  };

  for (const e of events) {
    while (unanchored.length && Date.parse(unanchored[0].created_at) <= Date.parse(e.at)) {
      flush();
      const task = unanchored.shift()!;
      out.push({ kind: "work", id: task.id, task });
    }
    const d = e.data;
    if (e.kind === "tool_call") {
      const result = results.get(d.id);
      if (d.name === "mcp__crewbots__delegate" || d.name === "mcp__crewbots__start_thread") {
        flush();
        if (d.input?.review_of) continue; // a review shows inside the card of the work it reviews
        if (result?.data.is_error) out.push({ kind: "line", id: e.id, text: `Couldn't delegate: ${oneLine(result.data.output, 160)}`, icon: "alert", tone: "bad" });
        else if (workCalls.has(e.id)) {
          const task = workCalls.get(e.id)!;
          out.push({ kind: "work", id: task.id, task });
        }
      } else if (d.name === "mcp__crewbots__ask") {
        flush();
        if (result?.data.is_error) out.push({ kind: "line", id: e.id, text: `Couldn't ask: ${oneLine(result.data.output, 160)}`, icon: "alert", tone: "bad" });
        else {
          const id = String(result?.data.output ?? "").match(/\bAsked [^\n]*?\((t_[^)]+)\)/)?.[1];
          const task = id ? asked.get(id) : undefined;
          if (id) asked.delete(id);
          out.push({ kind: "asked", id: e.id, bot: d.input?.bot ?? "", question: d.input?.question ?? "", task });
        }
      } else if (d.name === "mcp__crewbots__post") {
        flush();
        out.push({ kind: "line", id: e.id, text: `Said in a group chat: ${oneLine(d.input?.text ?? "", 160)}`, icon: "message" });
      } else if (d.name === "mcp__crewbots__share" && d.input?.room) {
        flush(); // shared in a group chat, it shows there; here, a line says so
        if (result?.data.is_error) out.push({ kind: "line", id: e.id, text: `Couldn't share: ${oneLine(result.data.output, 160)}`, icon: "alert", tone: "bad" });
        else out.push({ kind: "line", id: e.id, text: `Shared in a group chat: ${(d.input.files ?? []).map((f: string) => f.split("/").at(-1)).join(", ")}`, icon: "message" });
      } else if (d.name === "mcp__crewbots__share" && result?.data.is_error) {
        flush();
        out.push({ kind: "line", id: e.id, text: `Couldn't share: ${oneLine(result.data.output, 160)}`, icon: "alert", tone: "bad" });
      } else if ((d.name === "mcp__crewbots__write_page" || d.name === "mcp__crewbots__edit_page") && d.input?.page) {
        flush();
        if (result?.data.is_error) out.push({ kind: "line", id: e.id, text: `Couldn't write ${d.input.page}: ${oneLine(result.data.output, 160)}`, icon: "alert", tone: "bad" });
        else out.push({ kind: "page", id: e.id, page: String(d.input.page), changed: d.name.endsWith("edit_page") });
      } else if (d.name === "mcp__crewbots__next_check" && !result?.data.is_error) {
        flush();
        out.push({ kind: "line", id: e.id, text: `Will look around again in ${minutes(Number(d.input?.minutes))}: ${oneLine(d.input?.why ?? "", 140)}`, icon: "repeat", gist: "planned when to look again" });
      } else if (d.name === "mcp__crewbots__set_name" && !result?.data.is_error) {
        flush();
        out.push({ kind: "line", id: e.id, text: `Took the name ${oneLine(String(d.input?.name ?? ""), 60)}`, icon: "check" });
      } else if (d.name === "mcp__crewbots__message") {
        flush();
        const to = tasks.get(d.input?.task_id)?.bot ?? "the task";
        out.push({ kind: "line", id: e.id, text: `Sent to ${to}: ${oneLine(d.input?.text ?? "", 160)}` });
      } else if (!SHOWN_ELSEWHERE.has(d.name)) {
        steps.push({ call: e, result });
      }
      continue;
    }
    switch (e.kind) {
      case "message":
        flush();
        if (d.from === "user") out.push({ kind: "you", id: e.id, at: e.at, text: d.text, files: d.attachments ?? [] });
        else if (String(d.from).startsWith("routine:")) out.push({ kind: "brief", id: e.id, at: e.at, from: `the ${String(d.from).slice(8)} routine`, text: String(d.text).replace(/^\[routine: [^\]]+\]\s*/, "") });
        else if (d.from === "room") continue;
        else if (d.from === "routine") out.push({ kind: "brief", id: e.id, at: e.at, from: `the ${tasks.get(e.task_id)?.routine ?? ""} routine`, text: d.text });
        else if (d.from !== "crewbots") out.push({ kind: "brief", id: e.id, at: e.at, from: d.from, text: d.text });
        break;
      case "text":
        flush();
        out.push({ kind: "say", id: e.id, at: e.at, text: d.text, tag: d.digest ? "Morning digest" : sayTag(d), raised: d.proactive_id, files: d.files });
        break;
      case "shared":
        flush();
        out.push({ kind: "say", id: e.id, at: e.at, text: d.text ?? "", files: d.attachments ?? [] });
        break;
      case "onboarding":
        if (d.offer) {
          flush();
          out.push({ kind: "offer", id: e.id, outcome: events.find((x) => x.kind === "onboarding" && !x.data.offer && x.id > e.id)?.data });
        }
        break;
      case "permission":
        flush();
        out.push({
          kind: "permission", id: e.id, permission: d.permission, bot: d.bot, for: d.for, taskId: d.task, ask: d.kind, what: d.what, why: d.why ?? "", folder: d.folder, here: d.task === e.task_id,
          allowed: events.find((x) => x.kind === "permission_answer" && x.data.permission === d.permission)?.data.allowed,
        });
        break;
      case "escalation":
        flush();
        out.push({ kind: "ask", id: e.id, taskId: e.task_id, question: d.question, choices: d.choices, multiple: d.multiple, answer: answerTo(events, e.id) });
        break;
      case "seat_escalation":
        flush();
        out.push({ kind: "ask", id: e.id, taskId: d.task, question: d.question, choices: d.choices, multiple: d.multiple,
          room: d.room, questionId: d.question_id, answer: events.find((x) => x.kind === "seat_escalation_answer" && x.data.question_id === d.question_id)?.data.text,
          closed: events.some((x) => x.kind === "seat_escalation_closed" && x.data.question_id === d.question_id) });
        break;
      case "handoff":
        flush();
        out.push({ kind: "handoff", id: e.id, data: d });
        break;
      case "review":
        flush();
        out.push({ kind: "verdict", id: e.id, data: d });
        break;
      case "worktree":
        out.push({ kind: "line", id: e.id, text: `Working on its own branch, ${d.branch}`, icon: "branch" });
        break;
      case "merge_proposed":
        flush();
        out.push({ kind: "line", id: e.id, text: `${d.by} proposed it for merging`, icon: "merge" });
        break;
      case "push_proposed":
        flush();
        out.push({ kind: "push", id: e.id, data: d, started: events.some((x) => x.kind === "push_started" && x.data.proposal === e.id), result: events.find((x) => x.kind === "push_result" && x.data.proposal === e.id)?.data, replaced: events.some((x) => x.kind === "push_replaced" && x.data.proposal === e.id) });
        break;
      case "replace_main_proposed":
        flush();
        out.push({ kind: "replace", id: e.id, data: d, started: events.some((x) => x.kind === "replace_main_started" && x.data.proposal === e.id), result: events.find((x) => x.kind === "replace_main_result" && x.data.proposal === e.id)?.data });
        break;
      case "dropped":
        flush();
        out.push({ kind: "line", id: e.id, text: `${d.by === "user" ? "You" : d.by} dropped it: ${d.reason}`, icon: "merge" });
        break;
      case "merge_withdrawn":
        flush();
        out.push({ kind: "line", id: e.id, text: `${d.by === "user" ? "You" : d.by} withdrew the merge proposal: ${d.reason}`, icon: "merge" });
        break;
      case "memory":
        flush();
        out.push({
          kind: "line",
          id: e.id,
          text: d.action === "state" ? "Kept this task's state" : d.by === "user" ? `You gave it a note: ${d.title}` : `${d.action === "remember" ? "Remembered" : "Forgot"}${d.scope === "crew" ? " for the crew" : ""}: ${d.title}`,
          icon: "check",
        });
        break;
      case "correction":
        flush();
        out.push({ kind: "line", id: e.id, text: `Counted as a correction: ${d.mistake}`, icon: "repeat" });
        break;
      case "held":
        flush();
        out.push({ kind: "held", id: e.id, taskId: e.task_id, reason: d.reason });
        break;
      case "released":
        flush();
        out.push({ kind: "line", id: e.id, text: "You let it go ahead", icon: "play" });
        break;
      case "rate_limit":
        if (d.status !== "ok") {
          flush();
          out.push({ kind: "line", id: e.id, text: limitText(d as Parameters<typeof limitText>[0]), icon: "alert", tone: d.status === "limited" ? "bad" : "warn" });
        }
        break;
      case "restarted":
        flush();
        out.push(
          d.again
            ? { kind: "line", id: e.id, text: "Cut off by a restart again while carrying on. Send a message to carry on.", icon: "alert", tone: "warn" }
            : { kind: "line", id: e.id, text: "The crew restarted mid-turn, so it carried on where it left off", icon: "play" },
        );
        break;
      case "rollover":
        flush();
        out.push({ kind: "line", id: e.id, text: `Continued in a fresh session from a briefing (the conversation had used ${d.percent}% of its context)`, gist: COMPACTED });
        break;
      case "flushed":
        flush();
        out.push({ kind: "line", id: e.id, text: d.logged ? `Set down where things stand, and ${d.logged} line${d.logged === 1 ? "" : "s"} in today's log` : "Set down where things stand", gist: COMPACTED });
        break;
      case "compacted":
        flush();
        out.push({ kind: "line", id: e.id, text: `${d.trigger === "auto" ? "The CLI compacted" : "Compacted"} the conversation${d.before && d.after ? ` (${tokens(d.before)} → ${tokens(d.after)} tokens)` : ""}`, gist: COMPACTED });
        break;
      case "started_on_own":
        flush();
        out.push({ kind: "line", id: e.id, text: `Started on its own${d.why ? `: ${d.why}` : ""}`, icon: "play" });
        break;
      case "consolidated":
        flush();
        out.push({ kind: "line", id: e.id, text: `${d.text}${d.commit ? ` · memory commit ${d.commit}` : ""}`, icon: "check", gist: "tidied up its memory" });
        break;
      case "fresh_session":
        flush();
        out.push({ kind: "line", id: e.id, text: d.why === "fix round" ? "Started this round in a fresh session, from a brief of the task, its handback and its review" : d.why === "idle" ? "Carried on in a fresh session from memory, since the conversation sat idle past its cache" : "Carried on in a fresh session from memory, since the conversation couldn't be compacted", gist: COMPACTED });
        break;
      case "merged":
        flush();
        out.push({ kind: "line", id: e.id, text: `Merged${d.automatic ? " automatically" : ""} as ${String(d.commit).slice(0, 7)}${d.forced ? ", without approval" : ""}${d.leftover_files?.length ? ` · Saved after the merge started, still uncommitted: ${d.leftover_files.join(", ")}` : ""}${d.leftover_check_error ? ` · Could not check for later saves: ${d.leftover_check_error}` : ""}`, icon: "check", tone: "good" });
        break;
      case "reverted":
        flush();
        out.push({ kind: "line", id: e.id, text: `${d.by === "user" ? "You" : d.by} reverted the merge, as ${String(d.revert_commit).slice(0, 7)}`, icon: "repeat" });
        break;
      case "merge_cleanup_failed":
        flush();
        out.push({ kind: "line", id: e.id, text: `Merged, but couldn't remove the worktree: ${d.reason}`, icon: "alert", tone: "bad" });
        break;
      case "done":
        if (d.is_error && recovered.has(e.id)) {
          flush();
          out.push({ kind: "line", id: e.id, text: `Tried again after: ${d.error}`, icon: "repeat" });
        } else if (d.is_error) {
          flush();
          out.push({ kind: "line", id: e.id, text: `Stopped: ${d.error}`, icon: "alert", tone: "bad" });
        }
        break;
      case "state_notice":
        flush();
        out.push(d.warn || String(d.message).startsWith("State full or unavailable") ? { kind: "line", id: e.id, text: oneLine(d.message, 200), icon: "alert", tone: "warn" } : { kind: "line", id: e.id, text: oneLine(d.message, 200), gist: WOKEN, wake: true });
        break;
      case "turn_start":
        flush();
        break;
      case "notice":
        out.push({ kind: "line", id: e.id, text: oneLine(d.message, 200) });
        break;
    }
  }
  flush();
  for (const task of unanchored) out.push({ kind: "work", id: task.id, task });
  for (const left of asked.values()) out.push({ kind: "asked", id: Number.MAX_SAFE_INTEGER - out.length, bot: left.bot, question: left.title, task: left });
  return out;
}

const startsWake = (e: StoredEvent) => e.kind === "state_notice" || (e.kind === "message" && e.data.from === "crewbots");

/** In a chat: the turns the crew started (a notice, a watchdog or look-around prompt, a teammate's result) are
 * the bot's own business; the daemon marks them `system: true` on their `turn_start`, and nothing is inferred
 * here (a turn_start without the flag is shown whole). One that ends silent shows nothing; one that ends in a reply shows the reply, with what
 * woke it folded into the reply's steps. Turns are the daemon's own (`turn_start` events). What a turn consumes is
 * what was recorded since the one before began, so a notice queued during a user's turn belongs to the next turn,
 * not to the user's. Warnings, errors, cards and questions always stay, and so does anything the user wrote. */
export function settleWakes(events: StoredEvent[], blocks: Block[]): Block[] {
  type Turn = { from: number; to: number; system: boolean; replied: boolean; wakes: Block[] };
  const turns: Turn[] = [];
  const consumer = new Map<number, Turn>(); // a wake event's id -> the turn that took it
  let pending: StoredEvent[] = [];
  for (const e of events) {
    if (e.kind === "turn_start") {
      const turn: Turn = { from: e.id, to: Infinity, system: e.data.system === true, replied: false, wakes: [] };
      if (turns.length) turns[turns.length - 1].to = e.id;
      turns.push(turn);
      // Whatever came before an unmarked first turn_start is from before turns were recorded: left as it was.
      if (turns.length > 1 || turn.system)
        for (const p of pending) {
          consumer.set(p.id, turn);
          if (p.kind === "message" && p.data.from === "crewbots") turn.wakes.push({ kind: "line", id: p.id, text: oneLine(p.data.text, 200), gist: WOKEN, wake: true });
        }
      pending = [];
    } else if (startsWake(e) || e.kind === "message") pending.push(e);
    else if ((e.kind === "text" || e.kind === "shared") && turns.length) turns[turns.length - 1].replied = true;
  }
  if (!turns.length) return blocks; // events from before turns were recorded
  const owner = (b: Block): Turn | undefined => {
    const id = b.id;
    if (typeof id !== "number") return undefined;
    return consumer.get(id) ?? turns.find((t) => id >= t.from && id < t.to);
  };
  const out: Block[] = [];
  for (const b of blocks) {
    const t = owner(b);
    if (b.kind === "line" && b.wake) {
      if (!t) {
        if (typeof b.id === "number" && b.id < turns[0].from) out.push(b); // from before turns were recorded
        continue; // else not taken by a turn yet
      }
      if (!t.system) out.push(b); // a user's turn: whatever reached it stays
      else if (t.replied) t.wakes.push(b);
    } else if (t && t.system && !t.replied && quiet(b)) continue;
    else if (b.kind === "say" && t && t.wakes.length) {
      let at = out.length;
      while (at > 0 && quiet(out[at - 1])) at--;
      out.splice(at, 0, ...t.wakes.splice(0).sort((x, y) => (x.id as number) - (y.id as number)));
      out.push(b);
    } else out.push(b);
  }
  return out;
}

/** What a question was answered with: the next message to its task from the user, if one came. */
function answerTo(events: StoredEvent[], id: number): string | undefined {
  return events.find((x) => x.id > id && x.kind === "message" && x.data.from === "user")?.data.text;
}

function minutes(n: number): string {
  if (!Number.isFinite(n)) return "a while";
  return n < 90 ? `${Math.round(n)} min` : n < 36 * 60 ? `${Math.round(n / 60)}h` : `${Math.round(n / 1440)} days`;
}

/** A run of work, in a few words: the notes kept, the pages written, what else it was, the steps, and the rest as
 * updates. */
export function summary(items: Block[]): string {
  const lines = items.flatMap((i) => (i.kind === "line" ? [i] : []));
  const notes = lines.filter((i) => i.icon === "check" && NOTE.test(i.text));
  const pages = [...new Set(items.flatMap((i) => (i.kind === "page" ? [i.page] : [])))];
  const steps = items.reduce((n, i) => n + (i.kind === "steps" ? i.calls.length : 0), 0);
  const rest = lines.filter((i) => !i.gist && !notes.includes(i)).length;
  const parts = [
    notes.length ? `kept ${count(notes.length, "note")}` : "",
    pages.length ? `wrote ${pages.length === 1 ? pages[0] : `${pages.length} pages`}` : "",
    ...new Set(lines.flatMap((i) => (i.gist ? [i.gist] : []))),
    steps ? count(steps, "step") : "",
    rest ? count(rest, "update") : "",
  ].filter(Boolean);
  const text = parts.join(" · ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function count(n: number, thing: string): string {
  return `${n} ${thing}${n === 1 ? "" : "s"}`;
}

/** What a message a bot sent on its own says it is: needed now, told from a group chat or thread, or a check-in. */
function sayTag(d: Record<string, any>): string | undefined {
  const tag = [d.level === "needs_you" && "Needs you", d.from && `From ${d.from}`].filter(Boolean).join(" · ");
  return tag || (d.checkin ? "Checked in on its own" : undefined);
}
