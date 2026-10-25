import { useMemo, useState } from "react";
import type { Bot, ConnectionStatus, Task } from "../types";
import { ACTIVE } from "../types";
import { StatusDot } from "./bits";

type Filter = "all" | "you" | "running";

/** Tasks that are waiting on the user: a decision, a merge, or a failure to look at. */
export function needsYou(task: Task): boolean {
  return (
    task.merge_state === "proposed" ||
    (!task.parent_id && (task.status === "needs_input" || task.status === "failed" || task.status === "interrupted"))
  );
}

export function Sidebar(props: {
  tasks: Map<string, Task>;
  bots: Bot[];
  selected: string | null;
  onSelect: (id: string) => void;
  onNewTask: () => void;
  status: ConnectionStatus;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const { roots, children } = useMemo(() => tree([...props.tasks.values()]), [props.tasks]);

  const counts = {
    you: [...props.tasks.values()].filter(needsYou).length,
    running: [...props.tasks.values()].filter((t) => ACTIVE.includes(t.status)).length,
  };
  const visible = roots.filter((root) => {
    if (filter === "all") return true;
    const family = [root, ...descendants(root.id, children)];
    return filter === "you" ? family.some(needsYou) : family.some((t) => ACTIVE.includes(t.status));
  });

  return (
    <aside className="sidebar">
      <header className="sidebar-head">
        <span className="brand">crewbots</span>
        <button className="primary small" onClick={props.onNewTask}>
          New task
        </button>
      </header>
      <nav className="filters">
        <FilterTab label="All" active={filter === "all"} onClick={() => setFilter("all")} />
        <FilterTab label="Needs you" count={counts.you} active={filter === "you"} onClick={() => setFilter("you")} />
        <FilterTab
          label="Running"
          count={counts.running}
          active={filter === "running"}
          onClick={() => setFilter("running")}
        />
      </nav>
      <div className="task-list">
        {visible.length === 0 && <p className="muted small pad">Nothing here.</p>}
        {visible.map((root) => (
          <TaskNode key={root.id} task={root} tree={children} depth={0} {...props} />
        ))}
      </div>
      <footer className="crew">
        <div className="crew-title">Crew</div>
        {props.bots.map((bot) => (
          <div key={bot.name} className="crew-bot" title={bot.description}>
            <span>{bot.name}</span>
            <span className={`backend ${bot.backend}`}>{bot.backend}</span>
          </div>
        ))}
        <div className="muted small conn" title={props.status.home}>
          {props.status.ownsDaemon ? "daemon started by the app" : "attached to a running daemon"}
        </div>
      </footer>
    </aside>
  );
}

function FilterTab(props: { label: string; count?: number; active: boolean; onClick: () => void }) {
  return (
    <button className={`filter ${props.active ? "active" : ""}`} onClick={props.onClick}>
      {props.label}
      {props.count ? <span className="count">{props.count}</span> : null}
    </button>
  );
}

function TaskNode(props: {
  task: Task;
  tree: Map<string | null, Task[]>;
  depth: number;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const { task } = props;
  const kids = props.tree.get(task.id) ?? [];
  return (
    <>
      <button
        className={`task-row ${props.selected === task.id ? "selected" : ""}`}
        style={{ paddingLeft: 12 + props.depth * 16 }}
        onClick={() => props.onSelect(task.id)}
        title={task.title}
      >
        <StatusDot status={task.status} />
        <span className="task-bot">{task.bot}</span>
        <span className="task-title">{task.title}</span>
        {task.merge_state === "proposed" && <span className="badge ready">merge?</span>}
        {task.merge_state === "merged" && <span className="badge merged">merged</span>}
        {task.status === "needs_input" && <span className="badge ask">asks</span>}
      </button>
      {kids.map((kid) => (
        <TaskNode key={kid.id} {...props} task={kid} depth={props.depth + 1} />
      ))}
    </>
  );
}

function tree(tasks: Task[]) {
  const ids = new Set(tasks.map((t) => t.id));
  const children = new Map<string | null, Task[]>();
  const byAge = [...tasks].sort((a, b) => a.created_at.localeCompare(b.created_at));
  for (const task of byAge) {
    const parent = task.parent_id && ids.has(task.parent_id) ? task.parent_id : null;
    children.set(parent, [...(children.get(parent) ?? []), task]);
  }
  const roots = [...(children.get(null) ?? [])].reverse(); // newest goal first; delegated work in the order given
  return { roots, children };
}

function descendants(id: string, children: Map<string | null, Task[]>): Task[] {
  return (children.get(id) ?? []).flatMap((kid) => [kid, ...descendants(kid.id, children)]);
}
