import { useEffect, useMemo, useRef, useState } from "react";
import { Avatar } from "../components/Avatar";
import { ago, Markdown } from "../components/bits";
import { Composer, TaskDetail } from "../components/TaskDetail";
import type { Crew } from "../state";
import { doing, oneLine } from "../state";
import type { StoredEvent, Task } from "../types";
import { ACTIVE } from "../types";

/** Chat-first: each goal is one conversation with the bot you gave it to; the crew's work shows up inside it. */
export function Conversation(props: { crew: Crew; onNewTask: () => void; selected: string | null; onSelect: (id: string) => void }) {
  const { crew } = props;
  const goal = props.selected ? rootOf(crew, props.selected) : crew.goals[0];
  const [open, setOpen] = useState<string | null>(null); // a delegated task opened in the side panel

  useEffect(() => {
    if (props.selected && goal && props.selected !== goal.id) setOpen(props.selected);
  }, [props.selected, goal?.id]);

  return (
    <div className={`conv ${open ? "with-panel" : ""}`}>
      <aside className="conv-rail">
        <button className="new-goal" onClick={props.onNewTask}>
          + New goal
        </button>
        {crew.inbox.length > 0 && (
          <div className="rail-needs">
            <div className="rail-label">Needs you</div>
            {crew.inbox.map((item) => (
              <button key={item.task.id} className="rail-need" onClick={() => props.onSelect(item.task.id)}>
                <span className={`need-dot ${item.reason}`} />
                <span className="rail-need-text">{item.task.title}</span>
              </button>
            ))}
          </div>
        )}
        <div className="rail-label">Goals</div>
        <div className="rail-goals">
          {crew.goals.map((g) => {
            const busy = [g, ...crew.family(g.id)].some((t) => ACTIVE.includes(t.status));
            return (
              <button
                key={g.id}
                className={`rail-goal ${goal?.id === g.id ? "on" : ""}`}
                onClick={() => {
                  setOpen(null);
                  props.onSelect(g.id);
                }}
              >
                <span className="rail-goal-title">{oneLine(g.title, 60)}</span>
                <span className="rail-goal-meta">
                  {busy ? <span className="live">working</span> : ago(g.updated_at)}
                </span>
              </button>
            );
          })}
        </div>
        <div className="rail-crew">
          {crew.bots.map((b) => {
            const current = crew.running.find((t) => t.bot === b.name);
            return (
              <div key={b.name} className="rail-bot" title={current ? `${b.name}: ${current.title}` : `${b.name}: idle`}>
                <Avatar name={b.name} bot={b} status={current?.status} size={24} />
                <span>{b.name}</span>
              </div>
            );
          })}
        </div>
      </aside>

      <main className="conv-main">
        {goal ? (
          <Thread crew={crew} goal={goal} onOpen={setOpen} onSelect={props.onSelect} />
        ) : (
          <Welcome onNewTask={props.onNewTask} />
        )}
      </main>

      {open && crew.tasks.get(open) && (
        <aside className="conv-panel">
          <TaskDetail crew={crew} task={crew.tasks.get(open)!} onSelect={setOpen} onClose={() => setOpen(null)} />
        </aside>
      )}
    </div>
  );
}

function rootOf(crew: Crew, id: string): Task | undefined {
  let task = crew.tasks.get(id);
  while (task?.parent_id && crew.tasks.has(task.parent_id)) task = crew.tasks.get(task.parent_id);
  return task;
}

type Entry =
  | { kind: "you"; at: string; text: string }
  | { kind: "say"; at: string; text: string }
  | { kind: "steps"; at: string; events: StoredEvent[] }
  | { kind: "work"; at: string; task: Task }
  | { kind: "report"; at: string; text: string }
  | { kind: "note"; at: string; text: string; tone?: string };

function Thread(props: { crew: Crew; goal: Task; onOpen: (id: string) => void; onSelect: (id: string) => void }) {
  const { crew, goal } = props;
  const events = crew.events.get(goal.id);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => crew.ensureEvents(goal.id), [goal.id, crew.ensureEvents]);

  const entries = useMemo(() => thread(events ?? [], crew.children.get(goal.id) ?? []), [events, crew.children, goal.id]);
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight });
  }, [entries.length]);

  const lead = crew.bots.find((b) => b.name === goal.bot);
  return (
    <>
      <header className="thread-head">
        <div className="thread-title">{oneLine(goal.title, 120)}</div>
        <div className="thread-crew">
          {[goal, ...crew.family(goal.id)]
            .map((t) => t.bot)
            .filter((b, i, all) => all.indexOf(b) === i)
            .map((name) => (
              <Avatar
                key={name}
                name={name}
                bot={crew.bots.find((b) => b.name === name)}
                status={[goal, ...crew.family(goal.id)].find((t) => t.bot === name && ACTIVE.includes(t.status))?.status}
                size={22}
              />
            ))}
        </div>
      </header>
      <div className="thread" ref={box}>
        <div className="thread-inner">
          {!events && <p className="soft">Loading…</p>}
          {entries.map((entry, i) => (
            <ThreadEntry key={i} entry={entry} crew={crew} lead={goal.bot} onOpen={props.onOpen} onSelect={props.onSelect} />
          ))}
          {ACTIVE.includes(goal.status) && (
            <div className="typing">
              <Avatar name={goal.bot} bot={lead} status={goal.status} size={22} />
              <span className="soft">{doing(crew.latest.get(goal.id)) || `${goal.bot} is working…`}</span>
            </div>
          )}
        </div>
      </div>
      <div className="thread-composer">
        <Composer crew={crew} task={goal} placeholder={`Message ${goal.bot}`} />
      </div>
    </>
  );
}

/** The goal's own transcript, condensed: what was said, tool use folded into steps, delegated work as live cards. */
function thread(events: StoredEvent[], children: Task[]): Entry[] {
  const out: Entry[] = [];
  let steps: StoredEvent[] = [];
  const flush = () => {
    if (steps.length) out.push({ kind: "steps", at: steps[0].at, events: steps });
    steps = [];
  };
  for (const e of events) {
    if (e.kind === "tool_call" || e.kind === "tool_result") {
      if (e.kind === "tool_call" && e.data.name === "mcp__crewbots__delegate") continue; // shown as its card
      if (e.kind === "tool_call") steps.push(e);
      continue;
    }
    if (e.kind === "message") {
      flush();
      if (e.data.from === "user") out.push({ kind: "you", at: e.at, text: e.data.text });
      else if (e.data.from === "crewbots") out.push({ kind: "report", at: e.at, text: e.data.text });
      else out.push({ kind: "you", at: e.at, text: `${e.data.from}: ${e.data.text}` });
    } else if (e.kind === "text") {
      flush();
      out.push({ kind: "say", at: e.at, text: e.data.text });
    } else if (e.kind === "escalation") {
      flush();
      out.push({ kind: "note", at: e.at, text: `Asks you: ${e.data.question}`, tone: "warn" });
    } else if (e.kind === "done" && e.data.is_error) {
      flush();
      out.push({ kind: "note", at: e.at, text: `Failed: ${e.data.error}`, tone: "bad" });
    }
  }
  flush();
  for (const child of children) out.push({ kind: "work", at: child.created_at, task: child });
  return out.sort((a, b) => a.at.localeCompare(b.at));
}

function ThreadEntry(props: {
  entry: Entry;
  crew: Crew;
  lead: string;
  onOpen: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  const { entry, crew } = props;
  const [open, setOpen] = useState(false);
  switch (entry.kind) {
    case "you":
      return (
        <div className="you">
          <div className="you-bubble pre">{entry.text}</div>
        </div>
      );
    case "say":
      return (
        <div className="say-row">
          <Avatar name={props.lead} bot={crew.bots.find((b) => b.name === props.lead)} size={22} />
          <div className="say-text">
            <Markdown text={entry.text} onSelect={props.onOpen} />
          </div>
        </div>
      );
    case "steps":
      return (
        <div className="steps">
          <button className="steps-toggle" onClick={() => setOpen(!open)}>
            {open ? "▾" : "▸"} {entry.events.length} step{entry.events.length === 1 ? "" : "s"}
            <span className="soft"> · {doing(entry.events[entry.events.length - 1])}</span>
          </button>
          {open && (
            <ul className="steps-list">
              {entry.events.map((e) => (
                <li key={e.id} className="mono">
                  {doing(e)}
                </li>
              ))}
            </ul>
          )}
        </div>
      );
    case "report":
      return (
        <div className="report">
          <button className="steps-toggle" onClick={() => setOpen(!open)}>
            {open ? "▾" : "▸"} Results came back to {props.lead}
          </button>
          {open && (
            <div className="report-body">
              <Markdown text={entry.text} onSelect={props.onOpen} />
            </div>
          )}
        </div>
      );
    case "note":
      return <div className={`thread-note ${entry.tone ?? ""}`}>{entry.text}</div>;
    case "work":
      return <WorkCard crew={crew} task={entry.task} onOpen={props.onOpen} />;
  }
}

export function WorkCard(props: { crew: Crew; task: Task; onOpen: (id: string) => void; inColumn?: boolean }) {
  const { crew, task, onOpen } = props;
  const bot = crew.bots.find((b) => b.name === task.bot);
  const verdicts = crew.verdicts.get(task.id) ?? [];
  const reviews = (crew.children.get(task.parent_id ?? "") ?? []).filter((t) => t.review_of === task.id);
  const active = ACTIVE.includes(task.status);
  const line = active ? doing(crew.latest.get(task.id)) : task.review_of ? reviewLine(crew, task) : oneLine(task.result ?? "", 160);
  return (
    <button className={`work ${task.status}`} onClick={() => onOpen(task.id)}>
      <div className="work-top">
        {!props.inColumn && <Avatar name={task.bot} bot={bot} status={task.status} size={26} />}
        <div className="work-who">
          {!props.inColumn && <span className="work-bot">{task.bot}</span>}
          <span className="soft">{task.review_of ? "review" : statusWord(task)}</span>
        </div>
        <span className="grow" />
        {verdicts.map((v) => (
          <span key={v.id} className={`tag ${v.data.verdict === "pass" ? "good" : "bad"}`}>
            {v.data.reviewer} {v.data.verdict}
          </span>
        ))}
        {!verdicts.length && reviews.some((r) => ACTIVE.includes(r.status)) && <span className="tag">in review</span>}
        {task.merge_state === "proposed" && <span className="tag accent">ready to merge</span>}
        {task.merge_state === "merged" && <span className="tag good">merged</span>}
      </div>
      <div className="work-title">{oneLine(task.title, 110)}</div>
      {line && <div className={`work-line ${active ? "live-text" : ""}`}>{line}</div>}
    </button>
  );
}

function reviewLine(crew: Crew, task: Task): string {
  const verdict = [...crew.recent].reverse().find((e) => e.kind === "review" && e.data.task_id === task.id);
  return verdict ? `${String(verdict.data.verdict).toUpperCase()}: ${oneLine(verdict.data.summary, 140)}` : "";
}

function statusWord(task: Task): string {
  return { queued: "queued", running: "working", idle: "done", needs_input: "asking", failed: "failed", cancelled: "stopped", interrupted: "interrupted" }[task.status];
}

function Welcome({ onNewTask }: { onNewTask: () => void }) {
  return (
    <div className="welcome">
      <h1>What should the crew work on?</h1>
      <p className="soft">Give the chief a goal. It splits the work, the crew does it in parallel, and it comes back to you.</p>
      <button className="primary" onClick={onNewTask}>
        New goal
      </button>
    </div>
  );
}
