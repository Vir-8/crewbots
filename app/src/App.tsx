import { useState } from "react";
import { NewTask } from "./components/NewTask";
import { Board } from "./layouts/Board";
import { Conversation } from "./layouts/Conversation";
import { Inbox } from "./layouts/Inbox";
import { useCrew } from "./state";
import type { ConnectionStatus } from "./types";

// Three ways to arrange the same crew, to try side by side before settling on one.
const LAYOUTS = { conversation: Conversation, board: Board, inbox: Inbox } as const;
type Layout = keyof typeof LAYOUTS;
const LABELS: Record<Layout, string> = { conversation: "Conversation", board: "Board", inbox: "Inbox" };

function remembered(): Layout {
  const asked = new URLSearchParams(location.search).get("layout");
  if (asked && asked in LAYOUTS) return asked as Layout;
  try {
    const saved = localStorage.getItem("crewbots.layout");
    if (saved && saved in LAYOUTS) return saved as Layout;
  } catch {
    // storage can be unavailable; the default is fine
  }
  return "conversation";
}

export function App() {
  const crew = useCrew();
  const [layout, setLayout] = useState<Layout>(remembered);
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get("task"));
  const [composing, setComposing] = useState(false);

  if (crew.status.state !== "connected") return <Connecting status={crew.status} />;
  const View = LAYOUTS[layout];
  const choose = (next: Layout) => {
    setLayout(next);
    try {
      localStorage.setItem("crewbots.layout", next);
    } catch {
      // not remembered, that's all
    }
  };

  return (
    <div className="shell">
      <div className="titlebar" data-tauri-drag-region>
        <span className="wordmark" data-tauri-drag-region>
          crewbots
        </span>
        <div className="switcher" role="tablist">
          {(Object.keys(LAYOUTS) as Layout[]).map((key) => (
            <button key={key} className={layout === key ? "on" : ""} onClick={() => choose(key)}>
              {LABELS[key]}
            </button>
          ))}
        </div>
        <span className="titlebar-right soft small">
          {crew.inbox.length ? `${crew.inbox.length} need${crew.inbox.length === 1 ? "s" : ""} you` : ""}
        </span>
      </div>
      {crew.error && (
        <div className="toast" onClick={crew.clearError}>
          {crew.error}
        </div>
      )}
      <div className="stage">
        <View crew={crew} onNewTask={() => setComposing(true)} selected={selected || null} onSelect={(id) => setSelected(id || null)} />
      </div>
      {composing && (
        <NewTask
          bots={crew.bots}
          onClose={() => setComposing(false)}
          onCreated={(task) => {
            setSelected(task.id);
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
      <div className="wordmark big">crewbots</div>
      <p className={failed ? "bad pre" : "soft"}>{status.message ?? "Connecting to your crew…"}</p>
    </div>
  );
}
