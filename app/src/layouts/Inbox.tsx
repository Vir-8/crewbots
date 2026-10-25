import { useEffect, useMemo } from "react";
import { Avatar } from "../components/Avatar";
import { ago } from "../components/bits";
import { TaskDetail } from "../components/TaskDetail";
import type { Crew, InboxItem } from "../state";
import { describe, oneLine } from "../state";
import { ACTIVE } from "../types";

const REASON: Record<InboxItem["reason"], string> = {
  merge: "Merge",
  question: "Question",
  failed: "Failed",
  interrupted: "Interrupted",
};

/** An inbox: what needs you on top, the crew's activity below, the selected thing on the right. j/k to move. */
export function Inbox(props: { crew: Crew; onNewTask: () => void; selected: string | null; onSelect: (id: string) => void }) {
  const { crew } = props;
  const feed = useMemo(
    () =>
      [...crew.recent]
        .reverse()
        .map((e) => ({ event: e, line: describe(e, crew.tasks) }))
        .filter((x) => x.line)
        .slice(0, 80),
    [crew.recent, crew.tasks],
  );
  const order = [...crew.inbox.map((i) => i.task.id), ...crew.running.map((t) => t.id), ...feed.map((f) => f.event.task_id)].filter(
    (id, i, all) => all.indexOf(id) === i,
  );
  const selected = props.selected && crew.tasks.has(props.selected) ? props.selected : order[0];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("textarea, input")) return;
      const at = order.indexOf(selected ?? "");
      if (e.key === "j" && at < order.length - 1) props.onSelect(order[at + 1]);
      if (e.key === "k" && at > 0) props.onSelect(order[at - 1]);
      if (e.key === "c") props.onNewTask();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [order.join(), selected]);

  const task = selected ? crew.tasks.get(selected) : undefined;
  return (
    <div className="inbox">
      <aside className="inbox-list">
        <header className="inbox-head">
          <h1>Inbox</h1>
          <button className="primary small" onClick={props.onNewTask} title="New goal (c)">
            New goal
          </button>
        </header>
        <div className="inbox-scroll">
          <div className="rail-label">Needs you · {crew.inbox.length}</div>
          {crew.inbox.length === 0 && <p className="soft small pad-s">Nothing waiting on you.</p>}
          {crew.inbox.map((item) => (
            <button
              key={item.task.id}
              className={`inbox-item ${selected === item.task.id ? "on" : ""}`}
              onClick={() => props.onSelect(item.task.id)}
            >
              <span className={`reason ${item.reason}`}>{REASON[item.reason]}</span>
              <span className="inbox-title">{oneLine(item.task.title, 80)}</span>
              <span className="inbox-sub soft">{oneLine(item.text, 90)}</span>
            </button>
          ))}

          {crew.running.length > 0 && (
            <>
              <div className="rail-label">Working now · {crew.running.length}</div>
              {crew.running.map((t) => (
                <button key={t.id} className={`inbox-item ${selected === t.id ? "on" : ""}`} onClick={() => props.onSelect(t.id)}>
                  <span className="inbox-row">
                    <Avatar name={t.bot} status={t.status} size={18} />
                    <span className="inbox-title">{oneLine(t.title, 70)}</span>
                  </span>
                </button>
              ))}
            </>
          )}

          <div className="rail-label">Activity</div>
          {feed.map(({ event, line }) => (
            <button
              key={event.id}
              className={`feed-item ${selected === event.task_id ? "on" : ""}`}
              onClick={() => props.onSelect(event.task_id)}
            >
              <span className={`feed-text ${line!.tone ?? ""}`}>{line!.text}</span>
              <span className="soft small">{ago(event.at)}</span>
            </button>
          ))}
        </div>
      </aside>
      <main className="inbox-detail">
        {task ? (
          <TaskDetail crew={crew} task={task} onSelect={props.onSelect} />
        ) : (
          <div className="welcome">
            <h1>All caught up</h1>
            <p className="soft">Nothing needs you. {crew.running.some((t) => ACTIVE.includes(t.status)) ? "The crew is working." : ""}</p>
          </div>
        )}
      </main>
    </div>
  );
}
