import type { ReactNode } from "react";
import type { Status } from "../types";

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
    parts.push(text.slice(last, match.index));
    const id = match[0];
    parts.push(
      <a key={`${id}-${match.index}`} className="task-link" onClick={() => onSelect(id)}>
        {id}
      </a>,
    );
    last = match.index! + id.length;
  }
  parts.push(text.slice(last));
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

/**
 * The Markdown bots write: paragraphs, headings, lists (nested, and numbered from where they start), fenced code,
 * `code` and **bold**. Built as React elements, never as HTML, so nothing a bot writes can run in the window.
 */
export function Markdown({ text, onSelect }: { text: string; onSelect: (id: string) => void }) {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let i = 0;
  const inline = (s: string) => <Inline text={s} onSelect={onSelect} />;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      i++;
      blocks.push(<pre key={blocks.length}>{code.join("\n")}</pre>);
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
      while (i < lines.length && lines[i].trim() && !/^(```|#{1,6}\s|\s*([-*]|\d+\.)\s)/.test(lines[i])) {
        para.push(lines[i]);
        i++;
      }
      blocks.push(<p key={blocks.length}>{inline(para.join("\n"))}</p>);
    }
  }
  return <div className="md">{blocks}</div>;
}

const ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
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
  return [ordered ? <ol key={key} start={first === 1 ? undefined : first}>{body}</ol> : <ul key={key}>{body}</ul>, i];
}

export function Inline({ text, onSelect }: { text: string; onSelect: (id: string) => void }) {
  const parts: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    parts.push(<Linked key={`t${last}`} text={text.slice(last, match.index)} onSelect={onSelect} />);
    const token = match[0];
    parts.push(
      token.startsWith("`") ? (
        <code key={`c${match.index}`}>{token.slice(1, -1)}</code>
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
