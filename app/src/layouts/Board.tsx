import { useState } from "react";
import { Avatar } from "../components/Avatar";
import { TaskDetail } from "../components/TaskDetail";
import type { Crew } from "../state";
import { doing, oneLine } from "../state";
import type { Task } from "../types";
import { ACTIVE } from "../types";
import { WorkCard } from "./Conversation";

/** A board of the crew: one column per bot, showing what each one is doing and has done. */
export function Board(props: { crew: Crew; onNewTask: () => void; selected: string | null; onSelect: (id: string) => void }) {
  const { crew } = props;
  const [goal, setGoal] = useState<string>("all");
  const scope = goal === "all" ? null : new Set([goal, ...crew.family(goal).map((t) => t.id)]);
  const tasks = [...crew.tasks.values()].filter((t) => !scope || scope.has(t.id));
  const open = props.selected ? crew.tasks.get(props.selected) : undefined;

  return (
    <div className={`board ${open ? "with-panel" : ""}`}>
      <header className="board-head">
        <h1>Crew</h1>
        <select value={goal} onChange={(e) => setGoal(e.target.value)}>
          <option value="all">All goals</option>
          {crew.goals.map((g) => (
            <option key={g.id} value={g.id}>
              {oneLine(g.title, 70)}
            </option>
          ))}
        </select>
        <span className="grow" />
        <span className="soft">
          {crew.running.length ? `${crew.running.length} working` : "Everyone is idle"}
        </span>
        <button className="primary" onClick={props.onNewTask}>
          New goal
        </button>
      </header>

      <div className="board-cols">
        {crew.bots.map((bot) => {
          const mine = tasks.filter((t) => t.bot === bot.name).sort((a, b) => rank(a) - rank(b) || b.updated_at.localeCompare(a.updated_at));
          const now = mine.find((t) => ACTIVE.includes(t.status));
          return (
            <section key={bot.name} className="col">
              <header className="col-head">
                <Avatar name={bot.name} bot={bot} status={now?.status} size={36} />
                <div>
                  <div className="col-name">{bot.name}</div>
                  <div className="soft small">
                    {bot.title} · {bot.backend}
                    {bot.model ? ` ${bot.model}` : ""}
                  </div>
                </div>
              </header>
              <div className={`col-now ${now ? "busy" : ""}`}>
                {now ? doing(crew.latest.get(now.id)) || "Working…" : "Idle"}
              </div>
              <div className="col-cards">
                {mine.slice(0, 12).map((t) => (
                  <WorkCard key={t.id} crew={crew} task={t} onOpen={props.onSelect} inColumn />
                ))}
                {!mine.length && <p className="soft small pad-s">No tasks yet.</p>}
              </div>
            </section>
          );
        })}
      </div>

      {crew.inbox.length > 0 && (
        <footer className="board-needs">
          <span className="rail-label">Needs you</span>
          {crew.inbox.map((item) => (
            <button key={item.task.id} className="need-pill" onClick={() => props.onSelect(item.task.id)}>
              <span className={`need-dot ${item.reason}`} />
              {oneLine(item.task.title, 50)}
            </button>
          ))}
        </footer>
      )}

      {open && (
        <aside className="board-panel">
          <TaskDetail crew={crew} task={open} onSelect={props.onSelect} onClose={() => props.onSelect("")} />
        </aside>
      )}
    </div>
  );
}

function rank(task: Task): number {
  if (ACTIVE.includes(task.status)) return 0;
  if (task.status === "needs_input" || task.merge_state === "proposed") return 1;
  return 2;
}
