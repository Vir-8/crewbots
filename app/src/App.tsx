import { useEffect, useState } from "react";
import { Composer } from "./components/Composer";
import { CrewPage } from "./components/CrewPage";
import { Icon } from "./components/Icon";
import { Panel } from "./components/Panel";
import { Sidebar } from "./components/Sidebar";
import { Thread } from "./components/Thread";
import type { Crew } from "./state";
import { doing, oneLine, useCrew } from "./state";
import type { ConnectionStatus, Task } from "./types";
import { ACTIVE } from "./types";

export function App() {
  const crew = useCrew();
  const initial = new URLSearchParams(location.search).get("task");
  const [goal, setGoal] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null); // a delegated task shown in the side panel
  const [ready, setReady] = useState(false);
  const [crewPage, setCrewPage] = useState(false);

  const rootOf = (id: string): Task | undefined => {
    let task = crew.tasks.get(id);
    while (task?.parent_id && crew.tasks.has(task.parent_id)) task = crew.tasks.get(task.parent_id);
    return task;
  };
  const openTask = (id: string) => {
    const root = rootOf(id);
    if (!root) return;
    setGoal(root.id);
    setOpen(root.id === id ? null : id);
    setCrewPage(false);
  };

  // Open on the most recent goal, or on the task asked for in the URL.
  useEffect(() => {
    if (ready || crew.tasks.size === 0) return;
    if (initial && crew.tasks.has(initial)) openTask(initial);
    else setGoal(crew.goals[0]?.id ?? null);
    setReady(true);
  }, [crew.tasks.size]);

  if (crew.status.state !== "connected") return <Connecting status={crew.status} />;
  const current = goal ? crew.tasks.get(goal) : undefined;
  const panel = open ? crew.tasks.get(open) : undefined;

  return (
    <div className={`app ${panel ? "with-panel" : ""}`}>
      <Sidebar
        crew={crew}
        goal={crewPage ? undefined : current?.id ?? null}
        onGoal={(id) => {
          setGoal(id);
          setOpen(null);
          setCrewPage(false);
        }}
        onOpen={openTask}
        crewPage={crewPage}
        onCrew={() => {
          setCrewPage(true);
          setOpen(null);
        }}
      />
      <main className="main">
        {crewPage ? (
          <CrewPage crew={crew} onOpen={openTask} />
        ) : current ? (
          <Conversation crew={crew} goal={current} onOpen={openTask} />
        ) : (
          <NewGoal crew={crew} onStarted={(task) => setGoal(task.id)} />
        )}
      </main>
      {panel && <Panel crew={crew} task={panel} onOpen={openTask} onClose={() => setOpen(null)} />}
      {crew.error && (
        <div className="toast" onClick={crew.clearError}>
          <Icon name="alert" size={14} />
          {crew.error}
        </div>
      )}
    </div>
  );
}

function Conversation({ crew, goal, onOpen }: { crew: Crew; goal: Task; onOpen: (id: string) => void }) {
  const working = crew.family(goal.id).filter((t) => ACTIVE.includes(t.status));
  return (
    <>
      <header className="topbar" data-tauri-drag-region>
        <span className="topbar-title" data-tauri-drag-region>
          {oneLine(goal.title, 90)}
        </span>
        {working.length > 0 && (
          <button className="topbar-working" onClick={() => onOpen(working[0].id)} title={doing(crew.latest.get(working[0].id))}>
            <span className="working-dot small" />
            {working.map((t) => t.bot).join(", ")} working
          </button>
        )}
      </header>
      <Thread crew={crew} task={goal} onOpen={onOpen} />
      <div className="dock">
        <Composer
          placeholder={goal.status === "needs_input" ? `Answer ${goal.bot}` : `Message ${goal.bot}`}
          asking={goal.status === "needs_input"}
          onSend={(text) => crew.send(goal.id, text)}
        />
      </div>
    </>
  );
}

function NewGoal({ crew, onStarted }: { crew: Crew; onStarted: (task: Task) => void }) {
  const lead = crew.bots.find((b) => b.name === "chief")?.name ?? crew.bots[0]?.name ?? "";
  const [bot, setBot] = useState(lead);
  const [workspace, setWorkspace] = useState(() => {
    try {
      return localStorage.getItem("crewbots.workspace") ?? "";
    } catch {
      return "";
    }
  });
  useEffect(() => setBot((b) => b || lead), [lead]);
  return (
    <div className="new-goal">
      <header className="topbar" data-tauri-drag-region />
      <div className="new-goal-body">
        <h1>What should the crew work on?</h1>
        <p className="faint">
          Give {!bot || bot === "chief" ? "the chief" : bot} a goal. It splits the work across the crew, has it reviewed, and comes back to you.
        </p>
        <Composer
          autoFocus
          placeholder="Describe the outcome you want"
          bots={crew.bots}
          bot={bot}
          onBot={setBot}
          workspace={workspace}
          onWorkspace={(path) => {
            setWorkspace(path);
            try {
              localStorage.setItem("crewbots.workspace", path);
            } catch {
              // just not remembered
            }
          }}
          onSend={async (text) => onStarted(await crew.newTask(bot, text, workspace || undefined))}
        />
      </div>
    </div>
  );
}

function Connecting({ status }: { status: ConnectionStatus }) {
  const failed = status.state === "error" || status.state === "no-crew";
  return (
    <div className="connecting" data-tauri-drag-region>
      <div className="connecting-mark">crewbots</div>
      <p className={failed ? "bad" : "faint"}>{status.message ?? "Connecting to your crew…"}</p>
    </div>
  );
}
