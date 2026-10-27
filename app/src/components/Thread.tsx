import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Crew } from "../state";
import { doing, limitText, oneLine } from "../state";
import type { Attachment, StoredEvent, Task } from "../types";
import { ACTIVE } from "../types";
import { Files } from "./Attachments";
import { Boundary } from "./Boundary";
import { Markdown, tokens } from "./bits";
import { Finding, Marked, count, shown, useCurrentHit } from "./Find";
import { Icon } from "./Icon";
import { AskCard, Brief, Reactions, Steps, VerdictBlock } from "./ThreadParts";
import { WorkCard } from "./WorkCard";
import "../styles/chat.css";

const PAGE = 80; // blocks drawn when a conversation opens, and added each time you scroll up near the top

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
export function Thread(props: {
  crew: Crew;
  task: Task;
  onOpen: (id: string) => void;
  compact?: boolean;
  chat?: boolean;
  working?: string;
  find?: { query: string; current: number; onFound: (n: number) => void }; // ⌘F, lowercased
}) {
  const { crew, task } = props;
  const events = crew.events.get(task.id);
  const box = useRef<HTMLDivElement>(null);
  const col = useRef<HTMLDivElement>(null);
  const stuck = useRef(true);
  useEffect(() => crew.ensureEvents(task.id), [task.id, crew.ensureEvents]);

  const children = crew.children.get(task.id) ?? [];
  const blocks = useMemo(() => build(events ?? [], children, crew.tasks), [events, children, crew.tasks]);
  const active = ACTIVE.includes(task.status);

  // A long conversation draws only its latest blocks, and more as you scroll up to them, keeping your place: opening
  // one costs the same however long it is. `start` is the first block drawn; null follows the latest PAGE.
  const [start_, setStart] = useState<number | null>(null);
  useEffect(() => setStart(null), [task.id]);
  const start = start_ ?? Math.max(0, blocks.length - PAGE);
  const fromBottom = useRef<number | null>(null); // where you were, from the bottom, as earlier blocks went in above
  const earlier = (to: number) => {
    const el = box.current;
    if (to >= start || !el) return;
    fromBottom.current = el.scrollHeight - el.scrollTop;
    setStart(Math.max(0, to));
  };
  useLayoutEffect(() => {
    const el = box.current;
    if (el && fromBottom.current !== null) el.scrollTop = el.scrollHeight - fromBottom.current;
    fromBottom.current = null;
  }, [start]);
  const drawn = (i: number) => (i >= start ? col.current?.children[i - start + (start > 0 ? 1 : 0)] : undefined);

  // ⌘F: each match, found in what the blocks say (drawn or not), and which mark of its block's it is.
  const query = props.find?.query ?? "";
  const hits = useMemo(() => {
    const out: { block: number; el: () => Element | undefined; nth: number }[] = [];
    if (query) blocks.forEach((block, i) => {
      for (let nth = 0, n = count(said(block), query); nth < n; nth++) out.push({ block: i, el: () => drawn(i), nth });
    });
    return out;
  }, [blocks, query, start]);
  const current = props.find?.current ?? 0;
  useEffect(() => props.find?.onFound(hits.length), [hits.length, Boolean(props.find)]);
  useEffect(() => {
    const hit = hits[current];
    if (hit && hit.block < start) earlier(hit.block - 10); // draw back to it first
  }, [current, hits]);
  useCurrentHit(col, hits, current, query);

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
        if (el.scrollTop < 600 && start > 0) earlier(start - PAGE);
      }}
    >
      <div className={`thread-col ${props.compact ? "compact" : ""} ${props.chat ? "chat" : ""}`} ref={col}>
        <Finding.Provider value={query}>
        {!events && <div className="thread-loading" />}
        {start > 0 && (
          <button className="thread-earlier text-button" onClick={() => earlier(start - PAGE)}>
            Earlier messages
          </button>
        )}
        {blocks.slice(start).map((block) => (
          <Boundary key={`${block.kind}-${block.id}`} where={`a message (${block.kind} ${block.id} in ${task.id})`}>
            <BlockView block={block} crew={crew} lead={task.bot} onOpen={props.onOpen} />
          </Boundary>
        ))}
        </Finding.Provider>
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

/** What a block says, as it's drawn: where ⌘F looks. */
function said(block: Block): string {
  switch (block.kind) {
    case "you":
    case "brief":
    case "line":
      return block.text;
    case "say":
      return shown(block.text);
    case "ask":
      return block.question;
    case "asked":
      return block.question;
    case "handoff":
      return [block.data.done, block.data.open ? `Open: ${block.data.open}` : ""].join("\n");
    case "verdict":
      return block.data.summary ?? "";
    default:
      return "";
  }
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
        out.push({ kind: "say", id: e.id, text: d.text, tag: d.digest ? "Morning digest" : d.level === "needs_you" ? "Needs you" : d.checkin ? "Checked in on its own" : undefined, raised: d.proactive_id, files: d.files });
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
          text: d.action === "state" ? "Kept this task's state" : d.by === "user" ? `You gave it a note: ${d.title}` : `${d.action === "remember" ? "Remembered" : "Forgot"}${d.scope === "crew" ? " for the crew" : ""}: ${d.title}`,
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
          {block.text && <div className="you-text"><Marked text={block.text} /></div>}
          <Files items={block.files} mine />
        </div>
      );
    case "brief":
      return <Brief from={block.from} text={block.text} />;
    case "say":
      return (
        <div className="say">
          {block.tag && <div className={`say-tag ${block.tag === "Needs you" ? "urgent" : ""}`}>{block.tag}</div>}
          {block.text && <Markdown text={block.text} onSelect={props.onOpen} />}
          {block.files && <Files items={block.files} />}
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
          <span><Marked text={block.text} /></span>
        </div>
      );
    case "ask":
      return (
        <div className="ask">
          <div className="ask-head">
            <Icon name="question" size={15} />
            Needs your decision
          </div>
          <div className="ask-text"><Marked text={block.question} /></div>
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
          <div><Marked text={block.data.done} /></div>
          {block.data.output?.length > 0 && <div className="handoff-files">{block.data.output.join(" · ")}</div>}
          {block.data.open && <div className="handoff-open"><Marked text={`Open: ${block.data.open}`} /></div>}
        </div>
      );
    case "verdict":
      return <VerdictBlock data={block.data} />;
  }
}
