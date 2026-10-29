// The pieces of a thread that are more than a line of text: a brief, a verdict, a bot's steps (with the images its
// tools returned), a question to a teammate with its answer, and reactions to what a bot raised on its own.

import { useEffect, useRef, useState } from "react";
import type { Crew } from "../state";
import { describeCall } from "../state";
import type { Attachment, StoredEvent, Task } from "../types";
import { ACTIVE } from "../types";
import { Files } from "./Attachments";
import { labelOf } from "./Avatar";
import { fullDate, Markdown, stamp } from "./bits";
import { Marked } from "./Find";
import { Icon } from "./Icon";

export function Brief({ from, text, at }: { from: string; text: string; at: string }) {
  const long = text.length > 280;
  const [open, setOpen] = useState(!long);
  return (
    <div className="brief">
      <div className="brief-head">
        {from.startsWith("the ") ? `From ${from}` : `Brief from ${from}`}
        <time className="brief-time" dateTime={at} title={fullDate(at)}>
          {" · "}
          {stamp(at)}
        </time>
      </div>
      <div className={`brief-text ${open ? "" : "clamped"}`}><Marked text={text} /></div>
      {long && (
        <button className="text-button" onClick={() => setOpen(!open)}>
          {open ? "Less" : "More"}
        </button>
      )}
    </div>
  );
}

export function VerdictBlock({ data }: { data: Record<string, any> }) {
  const pass = data.verdict === "pass";
  return (
    <div className={`verdict ${pass ? "pass" : "fail"}`}>
      <div className="verdict-head">
        <Icon name={pass ? "check" : "x"} size={14} />
        <span>
          {data.reviewer} {pass ? "passed" : "requested changes"}
        </span>
        <span className="faint">version {data.version}</span>
      </div>
      <div className="verdict-text"><Marked text={data.summary} /></div>
      {data.findings?.length > 0 && (
        <ul className="findings">
          {data.findings.map((f: any, i: number) => (
            <li key={i}>
              <code>{f.location}</code> {f.problem}
              {f.scenario && <div className="faint">When {f.scenario}</div>}
              {f.proof ? <div className="faint">Shown by: {f.proof}</div> : "proof" in f && <div className="faint">Not reproduced</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const KIND: [RegExp, string, string][] = [
  [/^(Read|Glob|Grep|LS)$/, "Read", "file"],
  [/^(Edit|Write|MultiEdit|NotebookEdit|apply_patch)$/, "Edited", "file"],
  [/^(Bash|shell)$/, "Ran", "command"],
  [/^(WebSearch|WebFetch|web_search)$/, "Searched the web", ""],
  [/^mcp__crewbots__pr_(overview|diff)$/, "Read the pull request", ""],
  [/^mcp__crewbots__draft_comment$/, "Drafted", "comment"],
  [/^(view_image|image_generation)$/, "Looked at", "image"],
];

/** What a step did, as a verb and what it did it to; another app's MCP tool is named by its app ("Used Blender"). */
function kindOf(name: string): [string, string] {
  const known = KIND.find(([pattern]) => pattern.test(name));
  if (known) return [known[1], known[2]];
  const server = name.startsWith("mcp__") ? name.split("__")[1] : "";
  return server ? [`Used ${server.charAt(0).toUpperCase()}${server.slice(1)}`, ""] : ["Used", "tool"];
}

function summary(calls: { call: StoredEvent }[]): string {
  const counts = new Map<string, [string, string, number]>(); // verb, noun, how many
  for (const { call } of calls) {
    const [verb, noun] = kindOf(call.data.name);
    const n = call.data.name === "apply_patch" ? call.data.input?.changes?.length ?? 1 : 1;
    const key = `${verb}|${noun}`;
    counts.set(key, [verb, noun, (counts.get(key)?.[2] ?? 0) + n]);
  }
  return [...counts.values()].map(([verb, noun, n]) => (noun ? `${verb} ${n === 1 ? "a" : n} ${noun}${n > 1 ? "s" : ""}` : verb)).join(", ");
}

export function Steps({ calls }: { calls: { call: StoredEvent; result?: StoredEvent }[] }) {
  const [open, setOpen] = useState(false);
  const failed = calls.some((c) => c.result?.data.is_error);
  const images: Attachment[] = calls.flatMap((c) => c.result?.data.images ?? []); // a file it looked at, a screenshot
  return (
    <div className="steps">
      <button className="steps-head" onClick={() => setOpen(!open)}>
        <Icon name={open ? "chevronDown" : "chevronRight"} size={13} />
        <span>{summary(calls)}</span>
        {failed && <span className="steps-failed">one failed</span>}
      </button>
      {open && (
        <div className="steps-body">
          {images.length > 0 && <Files items={images} compact />}
          {calls.map(({ call, result }) => (
            <Step key={call.id} call={call} result={result} />
          ))}
        </div>
      )}
    </div>
  );
}

function Step({ call, result }: { call: StoredEvent; result?: StoredEvent }) {
  const [open, setOpen] = useState(false);
  const output = result?.data.output ? String(result.data.output) : "";
  return (
    <div className={`step ${result?.data.is_error ? "failed" : ""}`}>
      <button className="step-head" onClick={() => output && setOpen(!open)} disabled={!output}>
        <span className="step-arg">{describeCall(String(call.data.name), call.data.input ?? {})}</span>
      </button>
      {open && <pre className="step-output">{output.length > 4000 ? output.slice(0, 4000) + "\n…" : output}</pre>}
    </div>
  );
}

/** A question this bot asked a teammate, and the answer once it's in: a click opens the teammate's side of it. */
export function AskCard({ bot, question, task, onOpen }: { bot: string; question: string; task?: Task; onOpen: (id: string) => void }) {
  const answering = !task || ACTIVE.includes(task.status) || task.status === "held";
  const answer = task && !answering ? (task.status === "idle" ? task.result : task.error ?? task.status) : null;
  return (
    <div className={`ask-card ${task ? "" : "pending"}`} onClick={(event) => {
      if (task && !(event.target as HTMLElement).closest("button, a")) onOpen(task.id);
    }}>
      <div className="ask-card-content">
        {task ? <button type="button" className="ask-who ask-card-open" onClick={() => onOpen(task.id)}>Asked {labelOf(bot)}</button> : <div className="ask-who">Asked {labelOf(bot)}</div>}
        <div className="ask-card-question">
          <Icon name="question" size={15} />
          <AskMarkdown text={question} onOpen={onOpen} />
        </div>
        <div className="ask-who">Answer</div>
        {answering ? <div className="shimmer">Answering…</div> : <AskMarkdown text={answer ?? ""} onOpen={onOpen} />}
      </div>
    </div>
  );
}

export function AskMarkdown({ text, onOpen }: { text: string; onOpen: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const [long, setLong] = useState(false);
  const content = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = content.current;
    if (!node) return;
    const measure = () => {
      const lineHeight = parseFloat(getComputedStyle(node).lineHeight);
      setLong(node.scrollHeight > lineHeight * 4 + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [text]);
  return (
    <div className="ask-card-section">
      <div className={`ask-card-markdown ${expanded ? "" : "clamped"} ${long && !expanded ? "overflowing" : ""}`}><div ref={content}><Markdown text={text} onSelect={onOpen} /></div></div>
      {long && <button type="button" className="text-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "Show less" : "Show more"}</button>}
    </div>
  );
}

/** 👍 or 👎 on something a bot raised on its own: it becomes a note of the bot's, about what's worth raising. */
export function Reactions({ crew, id }: { crew: Crew; id: number }) {
  const [given, setGiven] = useState<"up" | "down" | null>(null);
  const react = (reaction: "up" | "down") => crew.react(id, reaction).then(() => setGiven(reaction), () => undefined);
  if (given) return <div className="say-reactions faint small">{given === "up" ? "Noted: worth raising" : "Noted: not worth raising"}</div>;
  return (
    <div className="say-reactions">
      <button className="reaction" onClick={() => react("up")} title="Worth raising: it notes that">
        👍
      </button>
      <button className="reaction" onClick={() => react("down")} title="Not worth raising: it notes that">
        👎
      </button>
    </div>
  );
}
