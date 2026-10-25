import { useCallback, useEffect, useMemo, useState } from "react";
import { api, bridge } from "./api";
import { NewTask } from "./components/NewTask";
import { Sidebar } from "./components/Sidebar";
import { TaskView } from "./components/TaskView";
import type { Bot, ConnectionStatus, Live, StoredEvent, Task } from "./types";

export function App() {
  const [status, setStatus] = useState<ConnectionStatus>({ state: "connecting", home: "" });
  const [bots, setBots] = useState<Bot[]>([]);
  const [tasks, setTasks] = useState<Map<string, Task>>(new Map());
  const [events, setEvents] = useState<Map<string, StoredEvent[]>>(new Map()); // only for tasks that were opened
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get("task"));
  const [composing, setComposing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    try {
      const [loadedBots, loadedTasks] = await Promise.all([api.bots(), api.tasks()]);
      setBots(loadedBots);
      setTasks(new Map(loadedTasks.map((t) => [t.id, t])));
      setEvents(new Map()); // reloaded on demand; anything cached may have missed live updates
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    bridge.status().then(setStatus);
    const offStatus = bridge.onStatus(setStatus);
    const offResync = bridge.onResync(loadAll);
    const offLive = bridge.onLive((message: Live) => {
      if (message.type === "task") {
        setTasks((current) => new Map(current).set(message.task.id, message.task));
      } else {
        const event = message.event;
        setEvents((current) => {
          const known = current.get(event.task_id);
          if (!known || known.some((e) => e.id === event.id)) return current;
          return new Map(current).set(event.task_id, [...known, event]);
        });
      }
    });
    return () => {
      offStatus();
      offResync();
      offLive();
    };
  }, [loadAll]);

  useEffect(() => {
    if (status.state === "connected") loadAll();
  }, [status.state, loadAll]);

  useEffect(() => {
    if (!selected || events.has(selected) || status.state !== "connected") return;
    api.events(selected).then(
      (loaded) => setEvents((current) => new Map(current).set(selected, loaded)),
      (e) => setError(String(e)),
    );
  }, [selected, events, status.state]);

  const task = selected ? tasks.get(selected) : undefined;
  const delegated = useMemo(
    () => (task ? [...tasks.values()].filter((t) => t.parent_id === task.id) : []),
    [task, tasks],
  );

  if (status.state !== "connected") return <Connecting status={status} />;

  return (
    <div className="app">
      <Sidebar
        tasks={tasks}
        bots={bots}
        selected={selected}
        onSelect={setSelected}
        onNewTask={() => setComposing(true)}
        status={status}
      />
      <main className="main">
        {error && (
          <div className="banner error" onClick={() => setError(null)}>
            {error}
          </div>
        )}
        {task ? (
          <TaskView
            key={task.id}
            task={task}
            tasks={tasks}
            delegated={delegated}
            events={events.get(task.id)}
            onSelect={setSelected}
            onError={setError}
          />
        ) : (
          <Empty onNewTask={() => setComposing(true)} count={tasks.size} />
        )}
      </main>
      {composing && (
        <NewTask
          bots={bots}
          onClose={() => setComposing(false)}
          onCreated={(created) => {
            setTasks((current) => new Map(current).set(created.id, created));
            setSelected(created.id);
            setComposing(false);
          }}
        />
      )}
    </div>
  );
}

function Connecting({ status }: { status: ConnectionStatus }) {
  const failed = status.state === "error" || status.state === "no-crew";
  return (
    <div className="connecting">
      <div className="brand">crewbots</div>
      <p className={failed ? "error-text" : "muted"}>{status.message ?? "Connecting to your crew…"}</p>
      {status.home && <p className="muted small">crew: {status.home}</p>}
    </div>
  );
}

function Empty({ onNewTask, count }: { onNewTask: () => void; count: number }) {
  return (
    <div className="empty">
      <h2>{count ? "Pick a task" : "Your crew is ready"}</h2>
      <p className="muted">Give the chief a goal and it splits the work across the crew, reviews it, and comes back to you.</p>
      <button className="primary" onClick={onNewTask}>
        New task
      </button>
    </div>
  );
}
