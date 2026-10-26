import { useEffect, useMemo, useRef, useState } from "react";
import type { Crew } from "../state";
import { describeCall, doing, limitText, oneLine } from "../state";
import type { Attachment, StoredEvent, Task } from "../types";
import { ACTIVE } from "../types";
import { MessageFiles } from "./Attachments";
import { Markdown } from "./bits";
import { Icon } from "./Icon";
import { WorkCard } from "./WorkCard";

type Block =
  | { kind: "you"; id: number; text: string; files: Attachment[] }
  | { kind: "brief"; id: number; from: string; text: string }
  | { kind: "say"; id: number; text: string }
  | { kind: "steps"; id: number; calls: { call: StoredEvent; result?: StoredEvent }[] }
  | { kind: "work"; id: number; task: Task }
  | { kind: "line"; id: number; text: string; icon?: "check" | "merge" | "branch" | "alert" | "play" | "message"; tone?: string }
  | { kind: "ask"; id: number; question: string }
  | { kind: "held"; id: number; taskId: string; reason: string }
  | { kind: "handoff"; id: number; data: Record<string, any> }
  | { kind: "verdict"; id: number; data: Record<string, any> };

// Crew tools that are shown as what they did (a card, a verdict, a question), not as steps.
const SHOWN_ELSEWHERE = new Set(
  ["delegate", "start_thread", "post", "handoff", "review", "escalate", "propose_merge", "message"].map((t) => `mcp__crewbots__${t}`),
);

/** A task's conversation, as people would tell it: what was said and decided, with the mechanics folded away. */
export function Thread(props: { crew: Crew; task: Task; onOpen: (id: string) => void; compact?: boolean; chat?: boolean }) {
  const { crew, task } = props;
  const events = crew.events.get(task.id);
  const box = useRef<HTMLDivElement>(null);
  const stuck = useRef(true);
  useEffect(() => crew.ensureEvents(task.id), [task.id, crew.ensureEvents]);

  const children = crew.children.get(task.id) ?? [];
  const blocks = useMemo(() => build(events ?? [], children, crew.tasks), [events, children, crew.tasks]);
  const active = ACTIVE.includes(task.status);

  useEffect(() => {
    if (stuck.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [blocks.length, active]);

  return (
    <div
      className="thread"
      ref={box}
      onScroll={(e) => {
        const el = e.currentTarget;
        stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      }}
    >
      <div className={`thread-col ${props.compact ? "compact" : ""} ${props.chat ? "chat" : ""}`}>
        {!events && <div className="thread-loading" />}
        {blocks.map((block) => (
          <BlockView key={`${block.kind}-${block.id}`} block={block} crew={crew} lead={task.bot} onOpen={props.onOpen} />
        ))}
        {active && (
          <div className="working">
            <span className="working-dot" />
            <span className="shimmer">{doing(crew.latest.get(task.id)) || `${task.bot} is working`}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function build(events: StoredEvent[], children: Task[], tasks: Map<string, Task>): Block[] {
  const out: Block[] = [];
  const delegated = children.filter((c) => !c.review_of); // in the order they were delegated, like the calls
  const results = new Map(events.filter((e) => e.kind === "tool_result").map((e) => [e.data.id, e]));
  let steps: { call: StoredEvent; result?: StoredEvent }[] = [];
  const flush = () => {
    if (steps.length) out.push({ kind: "steps", id: steps[0].call.id, calls: steps });
    steps = [];
  };

  for (const e of events) {
    const d = e.data;
    if (e.kind === "tool_call") {
      const result = results.get(d.id);
      if (d.name === "mcp__crewbots__delegate" || d.name === "mcp__crewbots__start_thread") {
        flush();
        if (d.input?.review_of) continue; // a review shows inside the card of the work it reviews
        if (result?.data.is_error) out.push({ kind: "line", id: e.id, text: `Couldn't delegate: ${oneLine(result.data.output, 160)}`, icon: "alert", tone: "bad" });
        else if (delegated.length) out.push({ kind: "work", id: e.id, task: delegated.shift()! });
      } else if (d.name === "mcp__crewbots__post") {
        flush();
        out.push({ kind: "line", id: e.id, text: `Said in a group chat: ${oneLine(d.input?.text ?? "", 160)}`, icon: "message" });
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
        if (d.from === "user") out.push({ kind: "you", id: e.id, text: d.text, files: d.attachments ?? [] });
        else if (String(d.from).startsWith("routine:")) out.push({ kind: "brief", id: e.id, from: `the ${String(d.from).slice(8)} routine`, text: String(d.text).replace(/^\[routine: [^\]]+\]\s*/, "") });
        else if (d.from === "room") continue;
        else if (d.from === "routine") out.push({ kind: "brief", id: e.id, from: `the ${tasks.get(e.task_id)?.routine ?? ""} routine`, text: d.text });
        else if (d.from !== "crewbots") out.push({ kind: "brief", id: e.id, from: d.from, text: d.text });
        break;
      case "text":
        flush();
        out.push({ kind: "say", id: e.id, text: d.text });
        break;
      case "escalation":
        flush();
        out.push({ kind: "ask", id: e.id, question: d.question });
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
      case "memory":
        flush();
        out.push({
          kind: "line",
          id: e.id,
          text: `${d.action === "remember" ? "Remembered" : "Forgot"}${d.scope === "crew" ? " for the crew" : ""}: ${d.title}`,
          icon: "check",
        });
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
      case "rollover":
        flush();
        out.push({ kind: "line", id: e.id, text: `Continued in a fresh session from a briefing (the conversation had used ${d.percent}% of its context)` });
        break;
      case "merged":
        flush();
        out.push({ kind: "line", id: e.id, text: `Merged as ${String(d.commit).slice(0, 7)}${d.forced ? ", without approval" : ""}`, icon: "check", tone: "good" });
        break;
      case "done":
        if (d.is_error) {
          flush();
          out.push({ kind: "line", id: e.id, text: `Stopped: ${d.error}`, icon: "alert", tone: "bad" });
        }
        break;
      case "notice":
        out.push({ kind: "line", id: e.id, text: oneLine(d.message, 200) });
        break;
    }
  }
  flush();
  for (const left of delegated) out.push({ kind: "work", id: Number.MAX_SAFE_INTEGER - out.length, task: left }); // no call seen
  return out;
}

function BlockView(props: { block: Block; crew: Crew; lead: string; onOpen: (id: string) => void }) {
  const { block } = props;
  switch (block.kind) {
    case "you":
      return (
        <div className="you">
          {block.text && <div className="you-text">{block.text}</div>}
          <MessageFiles items={block.files} />
        </div>
      );
    case "brief":
      return <Brief from={block.from} text={block.text} />;
    case "say":
      return (
        <div className="say">
          <Markdown text={block.text} onSelect={props.onOpen} />
        </div>
      );
    case "steps":
      return <Steps calls={block.calls} />;
    case "work":
      return <WorkCard crew={props.crew} task={block.task} onOpen={props.onOpen} />;
    case "line":
      return (
        <div className={`line ${block.tone ?? ""}`}>
          {block.icon && <Icon name={block.icon} size={14} />}
          <span>{block.text}</span>
        </div>
      );
    case "ask":
      return (
        <div className="ask">
          <div className="ask-head">
            <Icon name="question" size={15} />
            Needs your decision
          </div>
          <div className="ask-text">{block.question}</div>
        </div>
      );
    case "held": {
      const still = props.crew.tasks.get(block.taskId)?.status === "held";
      return (
        <div className={`hold-card ${still ? "" : "over"}`}>
          <div className="hold-head">
            <Icon name="pause" size={14} />
            {still ? "Waiting before its next turn" : "Waited before a turn"}
          </div>
          <div className="hold-text">{block.reason.charAt(0).toUpperCase() + block.reason.slice(1)}</div>
          {still && (
            <button className="button small hold-go" onClick={() => props.crew.release(block.taskId)}>
              Go ahead anyway
            </button>
          )}
        </div>
      );
    }
    case "handoff":
      return (
        <div className="handoff">
          <div className="handoff-head">Handed back</div>
          <div>{block.data.done}</div>
          {block.data.output?.length > 0 && <div className="handoff-files">{block.data.output.join(" · ")}</div>}
          {block.data.open && <div className="handoff-open">Open: {block.data.open}</div>}
        </div>
      );
    case "verdict":
      return <VerdictBlock data={block.data} />;
  }
}

function Brief({ from, text }: { from: string; text: string }) {
  const long = text.length > 280;
  const [open, setOpen] = useState(!long);
  return (
    <div className="brief">
      <div className="brief-head">{from.startsWith("the ") ? `From ${from}` : `Brief from ${from}`}</div>
      <div className={`brief-text ${open ? "" : "clamped"}`}>{text}</div>
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
      <div className="verdict-text">{data.summary}</div>
      {data.findings?.length > 0 && (
        <ul className="findings">
          {data.findings.map((f: any, i: number) => (
            <li key={i}>
              <code>{f.location}</code> {f.problem}
              {f.scenario && <div className="faint">When {f.scenario}</div>}
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
];

function summary(calls: { call: StoredEvent }[]): string {
  const counts = new Map<string, number>();
  for (const { call } of calls) {
    const kind = KIND.find(([pattern]) => pattern.test(call.data.name));
    const label = kind ? kind[1] + (kind[2] ? ` ${kind[2]}` : "") : "Used a tool";
    const n = call.data.name === "apply_patch" ? call.data.input?.changes?.length ?? 1 : 1;
    counts.set(label, (counts.get(label) ?? 0) + n);
  }
  return [...counts]
    .map(([label, n]) => {
      const [verb, noun] = label.split(" ");
      return noun && n > 1 ? `${verb} ${n} ${noun}s` : noun ? `${verb} ${n === 1 ? "a" : n} ${noun}` : label;
    })
    .join(", ");
}

function Steps({ calls }: { calls: { call: StoredEvent; result?: StoredEvent }[] }) {
  const [open, setOpen] = useState(false);
  const failed = calls.some((c) => c.result?.data.is_error);
  return (
    <div className="steps">
      <button className="steps-head" onClick={() => setOpen(!open)}>
        <Icon name={open ? "chevronDown" : "chevronRight"} size={13} />
        <span>{summary(calls)}</span>
        {failed && <span className="steps-failed">one failed</span>}
      </button>
      {open && (
        <div className="steps-body">
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
