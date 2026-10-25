import { useEffect, useMemo, useRef, useState } from "react";
import type { StoredEvent } from "../types";
import { Linked, Markdown, tokens } from "./bits";

type Item =
  | { kind: "event"; event: StoredEvent }
  | { kind: "tool"; call: StoredEvent; result?: StoredEvent };

/** Pairs each tool call with its result, so a tool use reads as one line. */
function items(events: StoredEvent[]): Item[] {
  const out: Item[] = [];
  const open = new Map<string, Extract<Item, { kind: "tool" }>>();
  for (const event of events) {
    if (event.kind === "tool_call") {
      const item = { kind: "tool" as const, call: event };
      open.set(event.data.id, item);
      out.push(item);
    } else if (event.kind === "tool_result" && open.has(event.data.id)) {
      open.get(event.data.id)!.result = event;
    } else {
      out.push({ kind: "event", event });
    }
  }
  return out;
}

export function Transcript({ events, onSelect }: { events: StoredEvent[] | undefined; onSelect: (id: string) => void }) {
  const list = useMemo(() => (events ? items(events) : []), [events]);
  const box = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);

  // Scroll only the transcript itself; scrollIntoView would scroll the whole window with it.
  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [list.length, follow]);

  if (!events) return <p className="muted pad">Loading…</p>;
  return (
    <div
      className="transcript"
      ref={box}
      onScroll={(e) => {
        const el = e.currentTarget;
        setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
      }}
    >
      {list.map((item) =>
        item.kind === "tool" ? (
          <ToolLine key={item.call.id} call={item.call} result={item.result} onSelect={onSelect} />
        ) : (
          <EventLine key={item.event.id} event={item.event} onSelect={onSelect} />
        ),
      )}
    </div>
  );
}

function EventLine({ event, onSelect }: { event: StoredEvent; onSelect: (id: string) => void }) {
  const d = event.data;
  switch (event.kind) {
    case "message":
      return <Message from={d.from} text={d.text} onSelect={onSelect} />;
    case "text":
      return (
        <div className="say">
          <Markdown text={d.text} onSelect={onSelect} />
        </div>
      );
    case "handoff":
      return (
        <div className="card handoff">
          <div className="card-title">Handoff</div>
          <Field label="Done" value={d.done} />
          {d.output?.length > 0 && <Field label="Output" value={d.output.join(", ")} mono />}
          {d.open && <Field label="Open" value={d.open} />}
          {d.needs && <Field label="Needs" value={d.needs} />}
        </div>
      );
    case "review":
      return (
        <div className={`card verdict ${d.verdict}`}>
          <div className="card-title">
            {d.reviewer}: {String(d.verdict).toUpperCase()} <span className="muted">· version {d.version}</span>
          </div>
          <div>{d.summary}</div>
          {d.findings?.length > 0 && (
            <ul className="findings">
              {d.findings.map((f: any, i: number) => (
                <li key={i}>
                  <span className="mono">{f.location}</span> {f.problem}
                  {f.scenario && <div className="muted small">Scenario: {f.scenario}</div>}
                </li>
              ))}
            </ul>
          )}
        </div>
      );
    case "escalation":
      return (
        <div className="card escalation">
          <div className="card-title">Asks for a decision</div>
          <div>{d.question}</div>
        </div>
      );
    case "merge_proposed":
      return (
        <div className="card proposed">
          <div className="card-title">Proposed for merging by {d.by}</div>
          <Markdown text={d.summary} onSelect={onSelect} />
        </div>
      );
    case "merged":
      return (
        <div className="note good">
          Merged as <span className="mono">{String(d.commit).slice(0, 12)}</span> into {d.into}
          {d.forced && " (without approval)"}
        </div>
      );
    case "worktree":
      return (
        <div className="note">
          Working in its own worktree on <span className="mono">{d.branch}</span>
        </div>
      );
    case "usage":
      return (
        <div className="note">
          {tokens(d.input_tokens)} in ({tokens(d.cached_input_tokens)} cached) · {tokens(d.output_tokens)} out
        </div>
      );
    case "done":
      return d.is_error ? <div className="note bad">Failed: {d.error}</div> : <div className="turn-end" />;
    case "notice":
      return <div className="note">{d.message}</div>;
    case "session_started":
      return null;
    default:
      return <div className="note mono">{event.kind}</div>;
  }
}

function Message({ from, text, onSelect }: { from: string; text: string; onSelect: (id: string) => void }) {
  const long = text.length > 600;
  const [open, setOpen] = useState(!long || from === "user");
  const who = from === "user" ? "You" : from === "crewbots" ? "crewbots" : from;
  return (
    <div className={`message from-${from === "user" ? "user" : from === "crewbots" ? "system" : "bot"}`}>
      <div className="message-from">{who}</div>
      <div className={open ? "" : "clipped"}>
        {from === "user" ? <div className="pre"><Linked text={text} onSelect={onSelect} /></div> : <Markdown text={text} onSelect={onSelect} />}
      </div>
      {long && (
        <button className="link" onClick={() => setOpen(!open)}>
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </div>
  );
}

function ToolLine(props: { call: StoredEvent; result?: StoredEvent; onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const { name, input } = props.call.data;
  const crew = String(name).startsWith("mcp__crewbots__");
  const label = crew ? String(name).replace("mcp__crewbots__", "") : name;
  const summary = summarize(input);
  const failed = props.result?.data.is_error;
  return (
    <div className={`tool ${crew ? "crew" : ""} ${failed ? "failed" : ""}`}>
      <button className="tool-head" onClick={() => setOpen(!open)}>
        <span className="tool-name">{label}</span>
        <span className="tool-summary">{summary}</span>
        {!props.result && <span className="muted small">running…</span>}
        {failed && <span className="badge fail">error</span>}
      </button>
      {open && (
        <div className="tool-detail">
          <pre>{JSON.stringify(input, null, 2)}</pre>
          {props.result && (
            <pre className={failed ? "bad" : ""}>
              <Linked text={props.result.data.output} onSelect={props.onSelect} />
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="field">
      <span className="field-label">{label}</span>
      <span className={mono ? "mono" : ""}>{value}</span>
    </div>
  );
}

function summarize(input: Record<string, unknown>): string {
  const first = input.command ?? input.file_path ?? input.pattern ?? input.query ?? input.bot ?? input.task_id ?? input.goal;
  const text = typeof first === "string" ? first : JSON.stringify(input);
  const oneLine = text.replace(/\s+/g, " ");
  return oneLine.length > 140 ? oneLine.slice(0, 140) + "…" : oneLine;
}
