import type { Crew, InboxItem } from "../state";
import { oneLine } from "../state";
import { ACTIVE } from "../types";
import { ago } from "./bits";
import { Icon } from "./Icon";

const NEED: Record<InboxItem["reason"], { label: string; icon: "merge" | "question" | "alert" }> = {
  merge: { label: "Ready to merge", icon: "merge" },
  question: { label: "Has a question", icon: "question" },
  failed: { label: "Failed", icon: "alert" },
  interrupted: { label: "Interrupted", icon: "alert" },
};

export function Sidebar(props: { crew: Crew; goal: string | null; onGoal: (id: string | null) => void; onOpen: (id: string) => void }) {
  const { crew } = props;
  return (
    <aside className="sidebar">
      <div className="sidebar-drag" data-tauri-drag-region />
      <button className={`side-row new ${props.goal === null ? "on" : ""}`} onClick={() => props.onGoal(null)}>
        <Icon name="compose" size={15} />
        New goal
      </button>

      {crew.inbox.length > 0 && (
        <section className="side-section">
          <div className="side-label">Needs you</div>
          {crew.inbox.map((item) => (
            <button key={item.task.id} className={`side-row need ${item.reason}`} onClick={() => props.onOpen(item.task.id)}>
              <Icon name={NEED[item.reason].icon} size={14} />
              <span className="side-text">
                <span className="side-title">{oneLine(rootTitle(crew, item.task.id), 60)}</span>
                <span className="side-sub">{NEED[item.reason].label}</span>
              </span>
            </button>
          ))}
        </section>
      )}

      <section className="side-section grow-section">
        <div className="side-label">Goals</div>
        <div className="side-scroll">
          {crew.goals.map((g) => {
            const busy = [g, ...crew.family(g.id)].some((t) => ACTIVE.includes(t.status));
            return (
              <button key={g.id} className={`side-row goal ${props.goal === g.id ? "on" : ""}`} onClick={() => props.onGoal(g.id)}>
                <span className="side-title">{oneLine(g.title, 64)}</span>
                {busy ? <span className="working-dot small" title="Working" /> : <span className="side-time">{ago(g.updated_at, true)}</span>}
              </button>
            );
          })}
          {crew.goals.length === 0 && <p className="side-empty">No goals yet.</p>}
        </div>
      </section>

      <section className="side-crew">
        {crew.bots.map((b) => {
          const working = crew.running.some((t) => t.bot === b.name);
          return (
            <div key={b.name} className="crew-row" title={b.description}>
              <span className={`crew-dot ${working ? "on" : ""}`} />
              <span className="crew-name">{b.name}</span>
              <span className="crew-backend">{b.backend}</span>
            </div>
          );
        })}
      </section>
    </aside>
  );
}

function rootTitle(crew: Crew, id: string): string {
  let task = crew.tasks.get(id);
  while (task?.parent_id && crew.tasks.has(task.parent_id)) task = crew.tasks.get(task.parent_id);
  return task?.title ?? "";
}
