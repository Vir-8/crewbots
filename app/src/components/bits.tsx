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
 * The Markdown bots write: paragraphs, headings, lists, fenced code, `code` and **bold**. Built as React elements,
 * never as HTML, so nothing a bot writes can run in the window.
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
    } else if (/^\s*([-*]|\d+\.)\s/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, ""));
        i++;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(<List key={blocks.length}>{items.map((item, j) => <li key={j}>{inline(item)}</li>)}</List>);
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
