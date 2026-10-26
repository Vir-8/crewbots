import { useEffect, useState } from "react";
import { DirectChat, GroupChat } from "./components/ChatView";
import { Icon } from "./components/Icon";
import { Panel } from "./components/Panel";
import { Profile } from "./components/Profile";
import { Sidebar } from "./components/Sidebar";
import { CodeReview } from "./pages/CodeReview";
import { CrewBots } from "./pages/CrewBots";
import { Routines } from "./pages/Routines";
import { Settings } from "./pages/Settings";
import { Usage } from "./pages/Usage";
import type { Crew } from "./state";
import { useCrew } from "./state";
import type { ConnectionStatus, Task } from "./types";

type Side = { kind: "profile" } | { kind: "thread"; id: string } | null;

export function App() {
  const crew = useCrew();
  const initial = new URLSearchParams(location.search).get("task");
  const [current, setCurrent] = useState<string | null>(null); // a chat's key, "review", or a page ("page:crew", ...)
  const [editing, setEditing] = useState<string | null>(null); // a bot being edited on the Crew page; "" for a new one
  const [side, setSide] = useState<Side>(null);
  const [newGroup, setNewGroup] = useState(false);
  const [sideHidden, setSideHidden] = useState(false);
  // Code review needs the room: in a smaller window, the sidebar steps aside when you open it.
  useEffect(() => {
    if (current === "review" && window.innerWidth < 1280) setSideHidden(true);
  }, [current]);

  /** Opens any task where it lives: a chat or a room, or a thread beside the chat it came from. */
  const openTask = (id: string) => {
    let task: Task | undefined = crew.tasks.get(id);
    if (!task) return;
    let root = task;
    while (root.parent_id && crew.tasks.has(root.parent_id)) root = crew.tasks.get(root.parent_id)!;
    if (root.kind === "chat") setCurrent(`bot:${root.bot}`);
    else if (root.kind === "seat" && root.room_id) setCurrent(`room:${root.room_id}`);
    setSide(task.kind === "thread" ? { kind: "thread", id } : null);
  };

  // Open on the most recent conversation, or the task asked for in the URL.
  useEffect(() => {
    if (current || crew.chats.length === 0) return;
    if (initial && crew.tasks.has(initial)) openTask(initial);
    else setCurrent([...crew.chats].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0].key);
  }, [crew.chats.length, crew.tasks.size]);

  if (crew.status.state !== "connected") return <Connecting status={crew.status} />;
  const chat = crew.chats.find((c) => c.key === current);
  const thread = side?.kind === "thread" ? crew.tasks.get(side.id) : undefined;
  const profile = side?.kind === "profile" && chat?.kind === "direct" ? chat.bot : null;

  return (
    <div className={`app ${thread || profile ? "with-panel" : ""} ${profile ? "with-profile" : ""} ${current === "review" ? "reviewing" : ""} ${sideHidden ? "side-hidden" : ""}`}>
      {sideHidden && (
        <button className="icon-button side-toggle" onClick={() => setSideHidden(false)} title="Show the sidebar">
          <Icon name="panelClose" size={15} />
        </button>
      )}
      <Sidebar
        crew={crew}
        current={current}
        onChat={(key) => {
          setCurrent(key);
          if (side?.kind === "thread" || !key.startsWith("bot:")) setSide(null);
        }}
        onOpen={openTask}
        onNewGroup={() => setNewGroup(true)}
        onHide={() => setSideHidden(true)}
      />
      <main className="main">
        {current === "page:crew" ? (
          <CrewBots crew={crew} editing={editing} onEdit={setEditing} onChat={(bot) => setCurrent(`bot:${bot}`)} />
        ) : current === "page:routines" ? (
          <Routines crew={crew} onOpen={openTask} />
        ) : current === "page:usage" ? (
          <Usage crew={crew} />
        ) : current === "page:settings" ? (
          <Settings crew={crew} />
        ) : current === "review" ? (
          <CodeReview crew={crew} onSettings={() => setCurrent("page:settings")} />
        ) : chat?.kind === "direct" ? (
          <DirectChat crew={crew} chat={chat} onOpen={openTask} profileOpen={Boolean(profile)} onProfile={() => setSide(profile ? null : { kind: "profile" })} />
        ) : chat?.kind === "group" ? (
          <GroupChat crew={crew} chat={chat} onOpen={openTask} />
        ) : (
          <div className="thread" />
        )}
      </main>
      {thread && <Panel crew={crew} task={thread} onOpen={openTask} onClose={() => setSide(chat?.kind === "direct" ? { kind: "profile" } : null)} />}
      {profile && (
        <Profile
          crew={crew}
          bot={profile}
          onOpen={openTask}
          onClose={() => setSide(null)}
          onEdit={() => {
            setEditing(profile);
            setCurrent("page:crew");
            setSide(null);
          }}
        />
      )}
      {newGroup && (
        <NewGroup
          crew={crew}
          onClose={() => setNewGroup(false)}
          onCreated={(id) => {
            setNewGroup(false);
            setCurrent(`room:${id}`);
            setSide(null);
          }}
        />
      )}
      {crew.error && (
        <div className="toast" onClick={crew.clearError}>
          <Icon name="alert" size={14} />
          {crew.error}
        </div>
      )}
    </div>
  );
}

function NewGroup({ crew, onClose, onCreated }: { crew: Crew; onClose: () => void; onCreated: (roomId: string) => void }) {
  const [title, setTitle] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  const [lead, setLead] = useState("");
  const leader = members.includes(lead) ? lead : members[0] ?? "";
  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h2>New group chat</h2>
        <input className="dialog-input" autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Reports team" />
        <div className="dialog-label">Bots in it</div>
        <div className="dialog-members">
          {crew.bots.map((b) => (
            <label key={b.name} className={`member-option ${members.includes(b.name) ? "on" : ""}`}>
              <input
                type="checkbox"
                checked={members.includes(b.name)}
                onChange={(e) => setMembers(e.target.checked ? [...members, b.name] : members.filter((m) => m !== b.name))}
              />
              <span className="member-name">{b.name}</span>
              <span className="faint">{b.title}</span>
            </label>
          ))}
        </div>
        {members.length > 1 && (
          <label className="dialog-lead">
            <span className="faint">Answers by default</span>
            <select value={leader} onChange={(e) => setLead(e.target.value)}>
              {members.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </label>
        )}
        <div className="dialog-actions">
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button
            className="button primary"
            onClick={async () => {
              if (members.length === 0) return;
              const room = await crew.newRoom(title, members, leader);
              onCreated(room.id);
            }}
          >
            Create
          </button>
        </div>
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
