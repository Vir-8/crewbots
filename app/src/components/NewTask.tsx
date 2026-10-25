import { useState } from "react";
import { api, bridge } from "../api";
import type { Bot, Task } from "../types";

export function NewTask(props: { bots: Bot[]; onClose: () => void; onCreated: (task: Task) => void }) {
  const lead = props.bots.find((b) => b.name === "chief") ?? props.bots[0];
  const [bot, setBot] = useState(lead?.name ?? "");
  const [goal, setGoal] = useState("");
  const [workspace, setWorkspace] = useState(localStorage.getItem("crewbots.workspace") ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const chosen = props.bots.find((b) => b.name === bot);

  const create = async () => {
    setBusy(true);
    try {
      if (workspace) localStorage.setItem("crewbots.workspace", workspace);
      props.onCreated(await api.newTask(bot, goal.trim(), workspace || undefined));
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  };

  return (
    <div className="overlay" onClick={props.onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h2>New task</h2>
        <label>Give it to</label>
        <div className="bot-picker">
          {props.bots.map((b) => (
            <button key={b.name} className={`bot-option ${bot === b.name ? "active" : ""}`} onClick={() => setBot(b.name)}>
              <b>{b.name}</b>
              <span className={`backend ${b.backend}`}>{b.backend}</span>
            </button>
          ))}
        </div>
        {chosen && <p className="muted small">{chosen.description}</p>}
        <label>Goal</label>
        <textarea
          autoFocus
          rows={6}
          value={goal}
          placeholder="The outcome you want, what matters, and what done means"
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && goal.trim()) create();
          }}
        />
        <label>Workspace</label>
        <div className="row">
          <input value={workspace} placeholder="Folder or repo (optional)" onChange={(e) => setWorkspace(e.target.value)} />
          <button className="ghost" onClick={async () => setWorkspace((await bridge.pickFolder()) ?? workspace)}>
            Browse…
          </button>
        </div>
        {error && <p className="error-text">{error}</p>}
        <div className="row end">
          <button className="ghost" onClick={props.onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy || !goal.trim() || !bot} onClick={create}>
            Start
          </button>
        </div>
      </div>
    </div>
  );
}
