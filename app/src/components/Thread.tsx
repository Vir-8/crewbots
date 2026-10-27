import { useEffect, useMemo, useRef, useState } from "react";
import type { Crew } from "../state";
import { describeCall, doing, limitText, oneLine } from "../state";
import type { Attachment, StoredEvent, Task } from "../types";
import { ACTIVE } from "../types";
import { MessageFiles } from "./Attachments";
import { labelOf } from "./Avatar";
import { Boundary } from "./Boundary";
import { Markdown, tokens } from "./bits";
import { Icon } from "./Icon";
import { WorkCard } from "./WorkCard";
import "../styles/chat.css";

type Block =
  | { kind: "you"; id: number; text: string; files: Attachment[] }
  | { kind: "brief"; id: number; from: string; text: string }
  | { kind: "say"; id: number; text: string; tag?: string; raised?: number; files?: Attachment[] }
  | { kind: "steps"; id: number; calls: { call: StoredEvent; result?: StoredEvent }[] }
  | { kind: "work"; id: number; task: Task }
  | { kind: "asked"; id: number; bot: string; question: string; task?: Task }
  | { kind: "line"; id: number; text: string; icon?: "check" | "merge" | "branch" | "alert" | "play" | "message"; tone?: string }
  | { kind: "ask"; id: number; question: string }
  | { kind: "held"; id: number; taskId: string; reason: string }
  | { kind: "handoff"; id: number; data: Record<string, any> }
  | { kind: "verdict"; id: number; data: Record<string, any> };

// Crew tools that are shown as what they did (a card, a verdict, a question), not as steps.
const SHOWN_ELSEWHERE = new Set(
  ["delegate", "start_thread", "post", "share", "handoff", "review", "escalate", "propose_merge", "message", "ask"].map((t) => `mcp__crewbots__${t}`),
);

/** A task's conversation, as people would tell it: what was said and decided, with the mechanics folded away. */
export function Thread(props: { crew: Crew; task: Task; onOpen: (id: string) => void; compact?: boolean; chat?: boolean; working?: string }) {
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
          <Boundary key={`${block.kind}-${block.id}`} where={`a message (${block.kind} ${block.id} in ${task.id})`}>
            <BlockView block={block} crew={crew} lead={task.bot} onOpen={props.onOpen} />
          </Boundary>
        ))}
        {active && (
          <div className="working">
            <span className="working-dot" />
            <span className="shimmer">{(props.working && task.status !== "running" ? "" : doing(crew.latest.get(task.id))) || props.working || `${task.bot} is working`}</span>
          </div>
        )}
      </div>
    </div>
  );
}

function build(events: StoredEvent[], children: Task[], tasks: Map<string, Task>): Block[] {
  const out: Block[] = [];
  const delegated = children.filter((c) => !c.review_of && c.kind !== "review" && c.kind !== "ask"); // in the order they were delegated, like the calls; a review's reviewers show in its pane
  const asked = children.filter((c) => c.kind === "ask");
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
      } else if (d.name === "mcp__crewbots__ask") {
        flush();
        if (result?.data.is_error) out.push({ kind: "line", id: e.id, text: `Couldn't ask: ${oneLine(result.data.output, 160)}`, icon: "alert", tone: "bad" });
        else out.push({ kind: "asked", id: e.id, bot: d.input?.bot ?? "", question: d.input?.question ?? "", task: asked.shift() });
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
        out.push({ kind: "say", id: e.id, text: d.text, tag: d.digest ? "Morning digest" : d.level === "needs_you" ? "Needs you" : d.checkin ? "Checked in on its own" : undefined, raised: d.proactive_id });
        break;
      case "shared":
        flush();
        out.push({ kind: "say", id: e.id, text: d.text ?? "", files: d.attachments ?? [] });
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
          text: d.action === "state" ? "Kept this task's state" : `${d.action === "remember" ? "Remembered" : "Forgot"}${d.scope === "crew" ? " for the crew" : ""}: ${d.title}`,
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
        out.push({ kind: "line", id: e.id, text: `Continued in a fresh session from a briefing (the conversation had used ${d.percent}% of its context)` });
        break;
      case "flushed":
        flush();
        out.push({ kind: "line", id: e.id, text: d.logged ? `Set down where things stand, and ${d.logged} line${d.logged === 1 ? "" : "s"} in today's log` : "Set down where things stand" });
        break;
      case "compacted":
        flush();
        out.push({ kind: "line", id: e.id, text: `${d.trigger === "auto" ? "The CLI compacted" : "Compacted"} the conversation${d.before && d.after ? ` (${tokens(d.before)} → ${tokens(d.after)} tokens)` : ""}` });
        break;
      case "started_on_own":
        flush();
        out.push({ kind: "line", id: e.id, text: `Started on its own${d.why ? `: ${d.why}` : ""}`, icon: "play" });
        break;
      case "consolidated":
        flush();
        out.push({ kind: "line", id: e.id, text: `${d.text}${d.commit ? ` · memory commit ${d.commit}` : ""}`, icon: "check" });
        break;
      case "fresh_session":
        flush();
        out.push({ kind: "line", id: e.id, text: d.why === "idle" ? "Carried on in a fresh session from memory, since the conversation sat idle past its cache" : "Carried on in a fresh session from memory, since the conversation couldn't be compacted" });
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
  for (const left of asked) out.push({ kind: "asked", id: Number.MAX_SAFE_INTEGER - out.length, bot: left.bot, question: left.title, task: left });
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
          {block.tag && <div className={`say-tag ${block.tag === "Needs you" ? "urgent" : ""}`}>{block.tag}</div>}
          {block.text && <Markdown text={block.text} onSelect={props.onOpen} />}
          {block.files && <MessageFiles items={block.files} />}
          {block.raised && <Reactions crew={props.crew} id={block.raised} />}
        </div>
      );
    case "steps":
      return <Steps calls={block.calls} />;
    case "work":
      return <WorkCard crew={props.crew} task={block.task} onOpen={props.onOpen} />;
    case "asked":
      return <AskCard bot={block.bot} question={block.question} task={block.task} onOpen={props.onOpen} />;
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
  [/^mcp__crewbots__pr_(overview|diff)$/, "Read the pull request", ""],
  [/^mcp__crewbots__draft_comment$/, "Drafted", "comment"],
];

function summary(calls: { call: StoredEvent }[]): string {
  const counts = new Map<string, [string, string, number]>(); // verb, noun, how many
  for (const { call } of calls) {
    const [, verb, noun] = KIND.find(([pattern]) => pattern.test(call.data.name)) ?? [null, "Used", "tool"];
    const n = call.data.name === "apply_patch" ? call.data.input?.changes?.length ?? 1 : 1;
    const key = `${verb}|${noun}`;
    counts.set(key, [verb, noun, (counts.get(key)?.[2] ?? 0) + n]);
  }
  return [...counts.values()].map(([verb, noun, n]) => (noun ? `${verb} ${n === 1 ? "a" : n} ${noun}${n > 1 ? "s" : ""}` : verb)).join(", ");
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

/** A question this bot asked a teammate, and the answer once it's in: a click opens the teammate's side of it. */
function AskCard({ bot, question, task, onOpen }: { bot: string; question: string; task?: Task; onOpen: (id: string) => void }) {
  const answering = !task || ACTIVE.includes(task.status) || task.status === "held";
  const answer = task && !answering ? (task.status === "idle" ? task.result : task.error ?? task.status) : null;
  return (
    <div className={`ask-card ${task ? "" : "pending"}`} onClick={() => task && onOpen(task.id)} role="button" tabIndex={0}>
      <span className="ask-line">
        <Icon name="question" size={13} />
        <span className="ask-who">Asked {labelOf(bot)}:</span> {oneLine(question, 220)}
      </span>
      <span className="ask-line answer">
        {answering ? <span className="shimmer">Answering…</span> : <><span className="ask-who">Answer:</span> {oneLine(answer ?? "", 320)}</>}
      </span>
    </div>
  );
}

/** 👍 or 👎 on something a bot raised on its own: it becomes a note of the bot's, about what's worth raising. */
function Reactions({ crew, id }: { crew: Crew; id: number }) {
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
