import type { Crew, InboxItem } from "../state";
import { limitText, oneLine } from "../state";
import type { Routine } from "../types";
import { ACTIVE } from "../types";
import { ago } from "./bits";
import { Icon } from "./Icon";

const NEED: Record<InboxItem["reason"], { label: string; icon: "merge" | "question" | "alert" | "pause" }> = {
  merge: { label: "Ready to merge", icon: "merge" },
  question: { label: "Has a question", icon: "question" },
  held: { label: "Waiting on a budget", icon: "pause" },
  failed: { label: "Failed", icon: "alert" },
  interrupted: { label: "Interrupted", icon: "alert" },
};

export function Sidebar(props: {
  crew: Crew;
  goal: string | null | undefined; // undefined: no goal is showing
  onGoal: (id: string | null) => void;
  onOpen: (id: string) => void;
  crewPage: boolean;
  onCrew: () => void;
}) {
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
                <span className="side-sub">{item.reason === "held" ? waitingOn(item) : NEED[item.reason].label}</span>
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
                {g.routine && <Icon name="repeat" size={13} className="side-routine" />}
                <span className="side-title">{oneLine(g.title, 64)}</span>
                {busy ? <span className="working-dot small" title="Working" /> : <span className="side-time">{ago(g.updated_at, true)}</span>}
              </button>
            );
          })}
          {crew.goals.length === 0 && <p className="side-empty">No goals yet.</p>}
        </div>
      </section>

      <Routines crew={crew} onStarted={props.onGoal} />

      <section className="side-crew">
        <button className={`side-row crew-link ${props.crewPage ? "on" : ""}`} onClick={props.onCrew}>
          <span className="side-title">Crew</span>
          {crew.routines?.crew_error || Object.keys(crew.routines?.errors ?? {}).length ? <Icon name="alert" size={13} className="crew-link-alert" /> : null}
          <Icon name="chevronRight" size={13} />
        </button>
        {(crew.crewState?.limits ?? [])
          .filter((l) => l.status !== "ok")
          .map((l) => (
            <div key={`${l.plan}-${l.window}`} className={`plan-limit ${l.status}`}>
              {limitText(l)}
            </div>
          ))}
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

function Routines({ crew, onStarted }: { crew: Crew; onStarted: (id: string) => void }) {
  const routines = crew.routines?.routines ?? [];
  const problems = Object.entries(crew.routines?.errors ?? {});
  if (routines.length === 0 && problems.length === 0) return null;
  return (
    <section className="side-section routines">
      <div className="side-label">Routines</div>
      {routines.map((r) => (
        <div key={r.name} className={`routine-row ${r.enabled ? "" : "off"}`} title={r.description || r.when}>
          <span className="routine-text">
            <span className="routine-name">{r.name}</span>
            <span className="side-sub">{when(r)}</span>
          </span>
          {r.schedule && (
            <button
              className="routine-run"
              title={`Run ${r.name} now`}
              onClick={async () => onStarted((await crew.runRoutine(r.name)).id)}
            >
              <Icon name="play" size={12} />
            </button>
          )}
        </div>
      ))}
      {problems.map(([file, why]) => (
        <div key={file} className="routine-row broken" title={why}>
          <span className="routine-text">
            <span className="routine-name">{file}</span>
            <span className="side-sub">Couldn't be read</span>
          </span>
        </div>
      ))}
      {crew.routines?.github_error && <div className="routine-row broken side-sub">GitHub: {crew.routines.github_error}</div>}
    </section>
  );
}

function waitingOn(item: InboxItem): string {
  const plan = /the (\w+) plan's/.exec(item.text);
  return plan ? `Waiting for the ${plan[1]} plan's limit` : `${item.task.bot} is waiting on a budget`;
}

function when(r: Routine): string {
  if (!r.enabled) return "Off";
  if (r.on) return r.on === "github.review_requested" ? `On review requests in ${r.repo}` : `On failed checks in ${r.repo}`;
  if (!r.next_run_at) return r.when;
  const next = new Date(r.next_run_at);
  const days = Math.round((startOfDay(next) - startOfDay(new Date())) / 86_400_000);
  const time = next.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const day = days === 0 ? "Today" : days === 1 ? "Tomorrow" : next.toLocaleDateString(undefined, { weekday: "long" });
  return `${day} at ${time}`;
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

function rootTitle(crew: Crew, id: string): string {
  let task = crew.tasks.get(id);
  while (task?.parent_id && crew.tasks.has(task.parent_id)) task = crew.tasks.get(task.parent_id);
  return task?.title ?? "";
}
