import { createContext, useContext, useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import type { Status } from "../types";
import { Finding, Marked } from "./Find";
import { crewMentionNames } from "./Avatar";
import { Icon } from "./Icon";

export const STATUS_LABEL: Record<Status, string> = {
  queued: "queued",
  running: "running",
  idle: "done",
  needs_input: "needs input",
  failed: "failed",
  cancelled: "cancelled",
  interrupted: "interrupted",
  held: "waiting",
};

function StatusDot({ status }: { status: Status }) {
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

/** Crew mentions with find marks kept across their boundaries. Plain messages keep their code syntax. */
export function MentionText({ text, everyone = false, plain = false }: { text: string; everyone?: boolean; plain?: boolean }) {
  const query = useContext(Finding);
  const names = [...crewMentionNames(), ...(everyone ? ["everyone"] : [])].sort((a, b) => b.length - a.length);
  const escaped = names.map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const mentions: { start: number; end: number }[] = [];
  const code = plain ? [...text.matchAll(/```[\s\S]*?(?:```|$)|`[^`\n]*`/g)].map((m) => [m.index, m.index + m[0].length]) : [];
  if (escaped.length) {
    const pattern = new RegExp(`@(?:${escaped.join("|")})`, "giu");
    for (const m of text.matchAll(pattern)) {
      const start = m.index, end = start + m[0].length;
      if ((start && /[\p{L}\p{N}_.+%@-]/u.test(text[start - 1])) || /[\p{L}\p{N}_@-]/u.test(text[end] ?? "") || code.some(([a, b]) => start < b && end > a)) continue;
      mentions.push({ start, end });
    }
  }
  const draw = (start: number, end: number): ReactNode[] => {
    const parts: ReactNode[] = [];
    let at = start;
    for (const m of mentions) {
      const a = Math.max(start, m.start), b = Math.min(end, m.end);
      if (a >= b) continue;
      if (at < a) parts.push(text.slice(at, a));
      parts.push(<span className="mention" key={a}>{text.slice(a, b)}</span>);
      at = b;
    }
    if (at < end) parts.push(text.slice(at, end));
    return parts;
  };
  if (!query) return <>{draw(0, text.length)}</>;
  const parts: ReactNode[] = [];
  const lower = text.toLowerCase();
  let from = 0;
  for (let at = lower.indexOf(query); at >= 0; at = lower.indexOf(query, at + query.length)) {
    parts.push(...draw(from, at), <mark key={`hit${at}`} className="find-hit">{draw(at, at + query.length)}</mark>);
    from = at + query.length;
  }
  parts.push(...draw(from, text.length));
  return <>{parts}</>;
}

const TASK_ID = /\bt_[0-9a-f]{10}\b/g;

/** Text with task ids turned into links to those tasks; whitespace is kept. */
function Linked({ text, onSelect, everyone = false }: { text: string; onSelect: (id: string) => void; everyone?: boolean }) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(TASK_ID)) {
    parts.push(<MentionText key={`t${last}`} text={text.slice(last, match.index)} everyone={everyone} />);
    const id = match[0];
    parts.push(
      <a key={`${id}-${match.index}`} className="task-link" onClick={() => onSelect(id)}>
        <Marked text={id} />
      </a>,
    );
    last = match.index! + id.length;
  }
  parts.push(<MentionText key="end" text={text.slice(last)} everyone={everyone} />);
  return <>{parts}</>;
}

/** 1500 → "1.5K", 200000 → "200K", 1000000 → "1M", 1200000000 → "1.2bn". */
export function tokens(n: number): string {
  if (n >= 1e9) return `${+(n / 1e9).toFixed(1)}bn`;
  if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${+(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K`;
  return String(Math.round(n));
}

/** /Users/you/code → ~/code. */
export function tilde(path: string): string {
  return path.replace(/^\/Users\/[^/]+/, "~");
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

/** Which message the pointer is on, marked `hovered` so its time and copy button show: one at a time, by following the
 * pointer over a conversation, since the app's WebKit view can leave a :hover's reveal showing after the pointer goes. */
export function useHoveredMessage() {
  const current = useRef<Element | null>(null);
  const mark = (next: Element | null) => {
    if (next === current.current) return;
    current.current?.classList.remove("hovered");
    next?.classList.add("hovered");
    current.current = next;
  };
  return {
    onMouseMove: (e: MouseEvent) => mark((e.target as Element).closest(".you, .said, .room-message")),
    onMouseLeave: () => mark(null),
    clear: () => mark(null), // on scroll: what's under the pointer moved
  };
}

/** Under a message: when it was sent (the whole date on hover) and a button that copies what it says. Shown while the
 * pointer is on the message (`useHoveredMessage`). */
export function MessageMeta({ at, text }: { at?: string; text?: string }) {
  const [copied, copy] = useCopy();
  if (!at) return null;
  return (
    <div className="message-meta">
      <time dateTime={at} title={fullDate(at)}>
        {stamp(at)}
      </time>
      {text && (
        <button className="icon-button" onClick={() => copy(text)} title={copied ? "Copied" : "Copy"} aria-label="Copy">
          <Icon name={copied ? "check" : "copy"} size={13} />
        </button>
      )}
    </div>
  );
}

/** Whether something was just copied, for a moment after, and what copies it. */
function useCopy(): [boolean, (text: string) => void] {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = (text: string) =>
    void toClipboard(text).then(() => {
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1500);
    }, () => undefined);
  return [copied, copy];
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

export function CodeBlock({ text }: { text: string }) {
  const [copied, copy] = useCopy();
  return (
    <div className="code-block">
      <div className="code-block-bar">
        <button type="button" className="button small code-copy" onClick={() => copy(text)} aria-label={copied ? "Copied" : "Copy code"}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre><Marked text={text} /></pre>
    </div>
  );
}

/**
 * The Markdown bots write: paragraphs, headings, lists (nested, and numbered from where they start), fenced code,
 * `code` and **bold**. Built as React elements, never as HTML, so nothing a bot writes can run in the window.
 */
export function Markdown({ text, onSelect, everyone = false }: { text: string; onSelect: (id: string) => void; everyone?: boolean }) {
  const blocks: ReactNode[] = [];
  const lines = text.split(/\r\n?|\n|\u2028|\u2029/); // GitHub's text often has Windows line endings
  let i = 0;
  const inline = (s: string) => <Inline text={s} onSelect={onSelect} everyone={everyone} />;
  while (i < lines.length) {
    const line = lines[i];
    const at = i;
    if (line.startsWith("```")) {
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      i++;
      blocks.push(<CodeBlock key={blocks.length} text={code.join("\n")} />);
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

/** A link to one of a bot's pages: it opens the page where something can. */
export function PageLink({ page, label = page }: { page: string; label?: string }) {
  const open = useContext(PageOpener);
  return open ? (
    <a className="page-link" onClick={() => open(page)} title={`Open ${page}`}>
      <Marked text={label} />
    </a>
  ) : (
    <span className="page-link off">
      <Marked text={label} />
    </span>
  );
}

/** A [[Page]] link (or [[Page#Heading|what it says]]), as in Obsidian: the page it opens and the words it shows. */
function wikiLink(raw: string): { page: string; label: string } {
  const [target, label] = raw.slice(2, -2).split("|");
  return { page: target.split("#")[0].trim(), label: (label ?? target).trim() };
}

function Inline({ text, onSelect, everyone = false }: { text: string; onSelect: (id: string) => void; everyone?: boolean }) {
  const parts: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\[\[[^\]\n]+\]\])/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    parts.push(<Linked key={`t${last}`} text={text.slice(last, match.index)} onSelect={onSelect} everyone={everyone} />);
    const token = match[0];
    parts.push(
      token.startsWith("`") ? (
        <code key={`c${match.index}`}><Marked text={token.slice(1, -1)} /></code>
      ) : token.startsWith("[[") ? (
        <PageLink key={`p${match.index}`} {...wikiLink(token)} />
      ) : (
        <strong key={`b${match.index}`}>
          <Linked text={token.slice(2, -2)} onSelect={onSelect} everyone={everyone} />
        </strong>
      ),
    );
    last = match.index! + token.length;
  }
  parts.push(<Linked key="end" text={text.slice(last)} onSelect={onSelect} everyone={everyone} />);
  return <span className="pre">{parts}</span>;
}
