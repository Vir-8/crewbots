import { createContext, useContext, useState, type ReactNode } from "react";
import type { Status } from "../types";
import { Marked } from "./Find";
import { Icon } from "./Icon";

const STATUS_LABEL: Record<Status, string> = {
  queued: "queued",
  running: "running",
  idle: "done",
  needs_input: "needs input",
  failed: "failed",
  cancelled: "cancelled",
  interrupted: "interrupted",
  held: "waiting",
};

export function StatusDot({ status }: { status: Status }) {
  return <span className={`dot ${status}`} title={STATUS_LABEL[status]} />;
}

export function StatusPill({ status }: { status: Status }) {
  return (
    <span className={`pill ${status}`}>
      <StatusDot status={status} />
      {STATUS_LABEL[status]}
    </span>
  );
}

const TASK_ID = /\bt_[0-9a-f]{10}\b/g;

/** Text with task ids turned into links to those tasks; whitespace is kept. */
export function Linked({ text, onSelect }: { text: string; onSelect: (id: string) => void }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(TASK_ID)) {
    parts.push(<Marked key={`t${last}`} text={text.slice(last, match.index)} />);
    const id = match[0];
    parts.push(
      <a key={`${id}-${match.index}`} className="task-link" onClick={() => onSelect(id)}>
        <Marked text={id} />
      </a>,
    );
    last = match.index! + id.length;
  }
  parts.push(<Marked key="end" text={text.slice(last)} />);
  return <>{parts}</>;
}

export function tokens(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
}

export function ago(iso: string, short = false): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  const suffix = short ? "" : " ago";
  if (seconds < 60) return short ? "now" : "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m${suffix}`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h${suffix}`;
  return `${Math.floor(seconds / 86400)}d${suffix}`;
}

const BREAK_AFTER_MS = 60 * 60_000; // a centred time goes over a message this long after the one before it

const clock = (d: Date) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
const dayStart = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
// Calendar days from `d` to `now`, rounded: a day with a clock change isn't 24 hours.
const daysBefore = (d: Date, now: Date) => Math.round((dayStart(now) - dayStart(d)) / 86_400_000);

/** The day a time fell on: "Today", "Yesterday", "Monday" this week, "Oct 22" this year, "Oct 22, 2025" before. */
function dayName(d: Date, now: Date): string {
  const days = daysBefore(d, now);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return d.toLocaleDateString(undefined, { weekday: "long" });
  const year = d.getFullYear() === now.getFullYear() ? {} : { year: "numeric" as const };
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", ...year });
}

/** When a message was sent, the way chat apps say it: "15:29" today, "Yesterday 15:29", "Monday 15:29" this week,
 * "Oct 22, 15:29" this year, and "Oct 22, 2025" before. */
export function stamp(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const day = dayName(d, now);
  if (day === "Today") return clock(d);
  if (d.getFullYear() !== now.getFullYear()) return day;
  return daysBefore(d, now) < 7 ? `${day} ${clock(d)}` : `${day}, ${clock(d)}`;
}

/** The day and the time, for a break in a conversation: ["Yesterday", "15:29"]. */
export function dayAndTime(iso: string, now = new Date()): [string, string] {
  const d = new Date(iso);
  return [dayName(d, now), clock(d)];
}

/** Which of a conversation's messages, in order, get a centred time over them: the first, and any that came an hour
 * or more after the one before it, or on another day. Those without a time are passed over. */
export function timeBreaks(items: { at?: string }[]): Set<number> {
  const out = new Set<number>();
  let before: Date | null = null;
  items.forEach((item, i) => {
    if (!item.at) return;
    const at = new Date(item.at);
    if (!before || at.getTime() - before.getTime() >= BREAK_AFTER_MS || daysBefore(before, at) !== 0) out.add(i);
    before = at;
  });
  return out;
}

/** A break in a conversation where time has passed, centred over the message after it: "**Yesterday** 15:29". */
export function TimeBreak({ at }: { at: string }) {
  const [day, time] = dayAndTime(at);
  return (
    <div className="time-break">
      <time dateTime={at} title={fullDate(at)}>
        <b>{day}</b> {time}
      </time>
    </div>
  );
}

/** The whole date and time, for a tooltip: "Oct 22, 2026, 5:55 PM". */
export function fullDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** Under a message: when it was sent (the whole date on hover) and a button that copies what it says. Shown while the
 * pointer is on the message. */
export function MessageMeta({ at, text }: { at?: string; text?: string }) {
  const [copied, setCopied] = useState(false);
  if (!at) return null;
  const copy = () =>
    toClipboard(text ?? "").then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => undefined,
    );
  return (
    <div className="message-meta">
      <time dateTime={at} title={fullDate(at)}>
        {stamp(at)}
      </time>
      {text && (
        <button className="icon-button" onClick={() => void copy()} title={copied ? "Copied" : "Copy"} aria-label="Copy">
          <Icon name={copied ? "check" : "copy"} size={13} />
        </button>
      )}
    </div>
  );
}

/** Puts text on the clipboard, the older way where the page may not write to it directly. */
async function toClipboard(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.cssText = "position: fixed; opacity: 0";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    if (!copied) throw new Error("couldn't copy");
  }
}

/**
 * The Markdown bots write: paragraphs, headings, lists (nested, and numbered from where they start), fenced code,
 * `code` and **bold**. Built as React elements, never as HTML, so nothing a bot writes can run in the window.
 */
export function Markdown({ text, onSelect }: { text: string; onSelect: (id: string) => void }) {
  const blocks: ReactNode[] = [];
  const lines = text.split(/\r\n?|\n|\u2028|\u2029/); // GitHub's text often has Windows line endings
  let i = 0;
  const inline = (s: string) => <Inline text={s} onSelect={onSelect} />;
  while (i < lines.length) {
    const line = lines[i];
    const at = i;
    if (line.startsWith("```")) {
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      i++;
      blocks.push(<pre key={blocks.length}><Marked text={code.join("\n")} /></pre>);
    } else if (/^#{1,6}\s/.test(line)) {
      const Tag = line.startsWith("# ") || line.startsWith("## ") ? "h3" : "h4";
      blocks.push(<Tag key={blocks.length}>{inline(line.replace(/^#+\s*/, ""))}</Tag>);
      i++;
    } else if (ITEM.test(line)) {
      const [node, next] = list(lines, i, inline, blocks.length);
      blocks.push(node);
      i = next;
    } else if (!line.trim()) {
      i++;
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s)/.test(lines[i]) && !ITEM.test(lines[i])) {
        para.push(lines[i]);
        i++;
      }
      if (para.length) blocks.push(<p key={blocks.length}>{inline(para.join("\n"))}</p>);
    }
    if (i === at) {
      blocks.push(<p key={blocks.length}>{inline(line)}</p>); // never stuck on a line nothing above took
      i++;
    }
  }
  return <div className="md">{blocks}</div>;
}

const ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)/;
const indentOf = (line: string) => (/^\s*/.exec(line)?.[0] ?? "").replace(/\t/g, "    ").length;

/** The list starting at `start`, and the line after it. An item takes the lines indented under it, its own list
 * included, and a blank line doesn't end the list when the next item, or more of this one, follows it. */
function list(lines: string[], start: number, inline: (s: string) => ReactNode, key: number): [ReactNode, number] {
  const base = indentOf(lines[start]);
  const ordered = /\d/.test(ITEM.exec(lines[start])![2]);
  const items: { text: string[]; children: ReactNode[] }[] = [];
  let i = start;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      const after = lines[j] === undefined ? null : ITEM.exec(lines[j]);
      const more = j < lines.length && (indentOf(lines[j]) > base || (after && indentOf(lines[j]) === base && /\d/.test(after[2]) === ordered));
      if (!more) break;
      i = j;
      continue;
    }
    const item = ITEM.exec(line);
    const indent = indentOf(line);
    if (item && indent === base) {
      if (/\d/.test(item[2]) !== ordered) break; // a different kind of list starts here
      items.push({ text: [item[3]], children: [] });
      i++;
    } else if (item && indent > base && items.length) {
      const [child, next] = list(lines, i, inline, items.at(-1)!.children.length);
      items.at(-1)!.children.push(child);
      i = next;
    } else if (!item && items.length && (indent > base || lines[i - 1]?.trim()) && !/^(```|#{1,6}\s)/.test(line.trim())) {
      items.at(-1)!.text.push(line.trim()); // more of the item: indented under it, or carrying straight on
      i++;
    } else {
      break;
    }
  }
  const first = Number.parseInt(ITEM.exec(lines[start])![2], 10);
  const body = items.map((item, j) => (
    <li key={j}>
      {inline(item.text.join("\n"))}
      {item.children}
    </li>
  ));
  return [ordered ? <ol key={key} start={first === 1 ? undefined : first}>{body}</ol> : <ul key={key}>{body}</ul>, Math.max(i, start + 1)];
}

/** Opens one of a bot's pages from a [[link]] in what it wrote: whatever shows the text knows whose pages they are. */
export const PageOpener = createContext<((page: string) => void) | null>(null);

/** A [[Page]] link (or [[Page|what it says]]), as in Obsidian: it opens the page where something can. */
function PageLink({ raw }: { raw: string }) {
  const open = useContext(PageOpener);
  const [target, label] = raw.slice(2, -2).split("|");
  const page = target.split("#")[0].trim();
  const text = (label ?? target).trim();
  return open ? (
    <a className="page-link" onClick={() => open(page)} title={`Open ${page}`}>
      <Marked text={text} />
    </a>
  ) : (
    <span className="page-link off">
      <Marked text={text} />
    </span>
  );
}

export function Inline({ text, onSelect }: { text: string; onSelect: (id: string) => void }) {
  const parts: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[\[[^\]\n]+\]\])/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    parts.push(<Linked key={`t${last}`} text={text.slice(last, match.index)} onSelect={onSelect} />);
    const token = match[0];
    parts.push(
      token.startsWith("`") ? (
        <code key={`c${match.index}`}><Marked text={token.slice(1, -1)} /></code>
      ) : token.startsWith("[[") ? (
        <PageLink key={`p${match.index}`} raw={token} />
      ) : (
        <strong key={`b${match.index}`}>
          <Linked text={token.slice(2, -2)} onSelect={onSelect} />
        </strong>
      ),
    );
    last = match.index! + token.length;
  }
  parts.push(<Linked key="end" text={text.slice(last)} onSelect={onSelect} />);
  return <span className="pre">{parts}</span>;
}
