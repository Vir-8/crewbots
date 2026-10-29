import { Fragment, type ReactNode, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Crew } from "../state";
import { doing } from "../state";
import type { Task } from "../types";
import { ACTIVE } from "../types";
import { Files } from "./Attachments";
import { labelOf } from "./Avatar";
import { Boundary } from "./Boundary";
import { Markdown, MentionText, MessageMeta, PageLinkTo, TimeBreak, timeBreaks, useHoveredMessage } from "./bits";
import { Finding, Marked, count, useCurrentHit } from "./Find";
import { Icon } from "./Icon";
import { Offer } from "./Offer";
import { Permission } from "./Permission";
import { PushCard } from "./PushCard";
import { ReplaceMainCard } from "./ReplaceMainCard";
import { Question } from "./Question";
import { AskCard, Brief, Reactions, Steps, VerdictBlock } from "./ThreadParts";
import { type Block, build, fold, said, settleWakes, summary } from "./threadBlocks";
import { useBottomAnchor } from "./ui";
import { WorkCard } from "./WorkCard";
import "../styles/chat.css";
import "../styles/cards.css";

const PAGE = 80; // blocks drawn when a conversation opens, and added each time you scroll up near the top

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
  // In a chat, what a bot did between two messages (its steps, the notes it kept, the pages it wrote) folds into one
  // row, so the conversation reads as one; a thread beside it shows it all.
  const blocks = useMemo(() => {
    const built = build(events ?? [], children, crew.tasks);
    return props.chat ? fold(settleWakes(events ?? [], built)) : built;
  }, [events, children, crew.tasks, props.chat]);
  const active = ACTIVE.includes(task.status);
  // Where time has passed between messages, a centred day and time goes over the next one.
  const breaks = useMemo(() => timeBreaks(blocks.map((b) => ({ at: b.kind === "you" || b.kind === "say" ? b.at : undefined }))), [blocks]);

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
  const hovered = useHoveredMessage();
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
  useBottomAnchor(box);

  return (
    <div
      className="thread"
      ref={box}
      onScroll={(e) => {
        const el = e.currentTarget;
        stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
        if (el.scrollTop < 600 && start > 0) earlier(start - PAGE);
        hovered.clear();
      }}
      onMouseMove={hovered.onMouseMove}
      onMouseLeave={hovered.onMouseLeave}
    >
      <div className={`thread-col ${props.compact ? "compact" : ""} ${props.chat ? "chat" : ""}`} ref={col}>
        <Finding.Provider value={query}>
        {!events && <div className="thread-loading" />}
        {start > 0 && (
          <button className="thread-earlier text-button" onClick={() => earlier(start - PAGE)}>
            Earlier messages
          </button>
        )}
        {blocks.slice(start).map((block, i) => (
          <Boundary key={`${block.kind}-${block.id}`} where={`a message (${block.kind} ${block.id} in ${task.id})`}>
            <BlockView block={block} crew={crew} lead={task.bot} onOpen={props.onOpen} afterBreak={breaks.has(start + i)} run={props.chat ? runOf(blocks, start + i, breaks) : ""} />
          </Boundary>
        ))}
        </Finding.Provider>
        {active && (
          <div className="working-indicator">
            <span className="working-dot" />
            <span className="shimmer">{(props.working && task.status !== "running" ? "" : doing(crew.latest.get(task.id))) || props.working || `${labelOf(task.bot)} is thinking`}</span>
          </div>
        )}
      </div>
    </div>
  );
}

/** In a chat, where a message sits in a run of messages from one side with no time break between them, as classes:
 * `joined` when one comes before it, `continued` when one comes after. */
function runOf(blocks: Block[], i: number, breaks: Set<number>): string {
  const side = (b?: Block) => (b?.kind === "you" ? "you" : b?.kind === "say" || b?.kind === "offer" ? "bot" : null);
  const here = side(blocks[i]);
  if (!here) return "";
  return [side(blocks[i - 1]) === here && !breaks.has(i) ? "joined" : "", side(blocks[i + 1]) === here && !breaks.has(i + 1) ? "continued" : ""]
    .filter(Boolean)
    .join(" ");
}

function BlockView(props: { block: Block; crew: Crew; lead: string; onOpen: (id: string) => void; afterBreak: boolean; run?: string }) {
  const place = (base: string) => [base, props.afterBreak ? "after-break" : "", props.run ?? ""].filter(Boolean).join(" ");
  const { block } = props;
  switch (block.kind) {
    case "you":
      return (
        <div className={place("you")}>
          {props.afterBreak && <TimeBreak at={block.at} />}
          <div className="message-row">
            <div className="message-body">
              <Files items={block.files} mine />
              {block.text && <div className="you-text"><MentionText text={block.text} plain /></div>}
            </div>
            <MessageMeta at={block.at} text={block.text} />
          </div>
        </div>
      );
    case "brief":
      return <Brief from={block.from} text={block.text} at={block.at} />;
    case "say":
      return (
        <div className={place("said")}>
          {props.afterBreak && <TimeBreak at={block.at} />}
          <div className="message-row">
            <div className="say">
              {block.tag && <div className={`say-tag ${block.tag.startsWith("Needs you") ? "urgent" : ""}`}>{block.tag}</div>}
              {block.text && <Markdown text={block.text} onSelect={props.onOpen} />}
              {block.files && <Files items={block.files} />}
              {block.raised && <Reactions crew={props.crew} id={block.raised} />}
            </div>
            <MessageMeta at={block.at} text={block.text} />
          </div>
        </div>
      );
    case "steps":
      return <Steps calls={block.calls} />;
    case "page":
      return (
        <div className="line">
          <Icon name="file" size={14} />
          <span>
            {block.changed ? "Changed" : "Wrote"} <PageLinkTo page={block.page} />
          </span>
        </div>
      );
    case "offer":
      return <Offer bot={props.lead} outcome={block.outcome} run={props.run} />;
    case "activity":
      return <Folded items={block.items} render={(item) => <BlockView block={item} crew={props.crew} lead={props.lead} onOpen={props.onOpen} afterBreak={false} />} />;
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
      return <Question crew={props.crew} block={block} onOpen={props.onOpen} />;
    case "permission":
      return <Permission crew={props.crew} block={block} onOpen={props.onOpen} />;
    case "push":
      return <PushCard block={block} />;
    case "replace":
      return <ReplaceMainCard block={block} />;
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
          {block.data.output?.length > 0 && (
            <ul className="handoff-files">
              {block.data.output.map((out: string) => (
                <li key={out} title={out}>
                  {out.replace(/^\/Users\/[^/]+/, "~")}
                </li>
              ))}
            </ul>
          )}
          {block.data.open && <div className="handoff-open"><Marked text={`Open: ${block.data.open}`} /></div>}
          {block.data.evidence?.length > 0 && (
            <div className="handoff-evidence">
              <div>Evidence</div>
              <ul>{block.data.evidence.map((e: string, i: number) => <li key={i}><Marked text={e} /></li>)}</ul>
            </div>
          )}
        </div>
      );
    case "verdict":
      return <VerdictBlock data={block.data} />;
  }
}

/** What a bot did between two messages, folded into a line that says it in short: open it to see each part. Open
 * while ⌘F has a match inside. */
function Folded({ items, render }: { items: Block[]; render: (item: Block) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const query = useContext(Finding);
  const found = Boolean(query) && items.some((item) => count(said(item), query) > 0);
  const shown = open || found;
  return (
    <div className="steps folded">
      <button className="steps-head" onClick={() => setOpen(!open)}>
        <Icon name={shown ? "chevronDown" : "chevronRight"} size={13} />
        <span>{summary(items)}</span>
      </button>
      {shown && <div className="folded-body">{items.map((item) => <Fragment key={`${item.kind}-${item.id}`}>{render(item)}</Fragment>)}</div>}
    </div>
  );
}
