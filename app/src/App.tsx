import { useEffect, useRef, useState } from "react";
import { DirectChat, GroupChat } from "./components/ChatView";
import { Boundary } from "./components/Boundary";
import { CrewBanner } from "./components/CrewBanner";
import { Icon } from "./components/Icon";
import { Panel } from "./components/Panel";
import { Profile, RoomProfile } from "./components/Profile";
import { RailEdge, Resizer, useWidth } from "./components/Resizer";
import { Sidebar } from "./components/Sidebar";
import { CodeReview } from "./pages/CodeReview";
import { BotEditor, CrewBots } from "./pages/CrewBots";
import { Routines } from "./pages/Routines";
import { Settings } from "./pages/Settings";
import { Analytics } from "./pages/Analytics";
import { Tools } from "./pages/Tools";
import type { Crew } from "./state";
import { useCrew } from "./state";
import { settle } from "./components/ui";
import type { ConnectionStatus, Task } from "./types";
import "./styles/dialogs.css";

/** A preference kept in this browser profile, surviving restarts; a storage that won't work just forgets. */
function useRemembered(key: string, initial: boolean): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState(() => {
    try {
      const saved = localStorage.getItem(key);
      return saved === null ? initial : saved === "1";
    } catch {
      return initial;
    }
  });
  const set = (next: boolean) => {
    setValue(next);
    try {
      localStorage.setItem(key, next ? "1" : "0");
    } catch {
      // not remembered, but still applied
    }
  };
  return [value, set];
}

export function App() {
  const crew = useCrew();
  const initial = new URLSearchParams(location.search).get("task");
  const [current, setCurrent] = useState<string | null>(null); // a chat's key, "review", or a page ("page:crew", ...)
  const [editing, setEditing] = useState<string | null>(null); // a bot being edited, over whatever's open; "" for a new one
  const [thread_, setThread] = useState<string | null>(null); // a thread open beside its chat
  // The profile beside a bot's chat, or a group's beside its chat: on unless you turn it off, and remembered, wherever
  // you go in between.
  const [profileOn, setProfileOn] = useRemembered("crewbots.profile", true);
  const [newGroup, setNewGroup] = useState(false);
  const [sideOff, setSideOff] = useRemembered("crewbots.sidebar-hidden", false);
  // Code review needs the room: in a smaller window, the sidebar steps aside while you're there, without changing
  // your own choice for everywhere else.
  const [stepAside, setStepAside] = useState(false);
  useEffect(() => setStepAside(current === "review" && window.innerWidth < 1280), [current]);
  const sideHidden = sideOff || stepAside;
  // Widths you drag the sidebar and the panels to, kept; each edge's handle sets them.
  const shell = useRef<HTMLDivElement>(null);
  const [sideWidth, setSideWidth] = useWidth("sidebar");
  const [panelWidth, setPanelWidth] = useWidth("panel");
  const [profileWidth, setProfileWidth] = useWidth("profile");
  const host = () => shell.current;
  const part = (selector: string) => () => shell.current?.querySelector<HTMLElement>(`:scope > ${selector}`) ?? null;
  const setSideHidden = (hidden: boolean) => {
    setStepAside(false);
    setSideOff(hidden);
  };

  /** Opens any task where it lives: a chat or a room, or a thread beside the chat it came from. */
  const openTask = (id: string) => {
    let task: Task | undefined = crew.tasks.get(id);
    if (!task) return;
    let root = task;
    while (root.parent_id && crew.tasks.has(root.parent_id)) root = crew.tasks.get(root.parent_id)!;
    if (root.kind === "chat") setCurrent(`bot:${root.bot}`);
    else if (root.kind === "seat" && root.room_id) setCurrent(`room:${root.room_id}`);
    setThread(task.kind === "thread" ? id : null);
  };

  // Open on the most recent conversation, or the task asked for in the URL.
  useEffect(() => {
    if (current || crew.chats.length === 0) return;
    if (initial && crew.tasks.has(initial)) openTask(initial);
    else setCurrent([...crew.chats].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0].key);
  }, [crew.chats.length, crew.tasks.size]);

  if (crew.status.state !== "connected" && !crew.connectedOnce) return <Connecting status={crew.status} />;
  const chat = crew.chats.find((c) => c.key === current);
  const thread = thread_ ? crew.tasks.get(thread_) : undefined;
  const profile = !thread && profileOn && chat?.kind === "direct" && current === chat.key ? chat.bot : null;
  const room = !thread && profileOn && chat?.kind === "group" && current === chat.key ? chat.room : null;

  return (
    <div ref={shell} className={`app ${thread || profile || room ? "with-panel" : ""} ${profile || room ? "with-profile" : ""} ${current === "review" ? "reviewing" : ""} ${sideHidden ? "side-hidden" : ""}`}>
      {/* One place in both states, beside the window's buttons, so it's always under your pointer. */}
      <button
        className="icon-button side-toggle"
        onClick={(e) => {
          settle(e);
          setSideHidden(!sideHidden);
        }}
        title={sideHidden ? "Expand the sidebar" : "Collapse the sidebar"}
      >
        <Icon name="sidebar" size={15} />
      </button>
      <Boundary where="the sidebar" as="aside" className="sidebar">
      <Sidebar
        crew={crew}
        current={current}
        rail={sideHidden}
        onChat={(key) => {
          setCurrent(key);
          setThread(null);
        }}
        onOpen={openTask}
        onNewGroup={() => setNewGroup(true)}
        onExpand={() => setSideHidden(false)}
        edge={
          sideHidden ? (
            <RailEdge onExpand={() => setSideHidden(false)} />
          ) : (
            <Resizer label="Resize the sidebar" edge="right" width={sideWidth} min={200} max={440} cssVar="--side-user" host={host} pane={part(".sidebar")} flexible={part(".main")} onChange={setSideWidth} onCollapse={() => setSideHidden(true)} />
          )
        }
      />
      </Boundary>
      <main className="main">
        <Boundary where={`the main view (${current ?? "none"})`} key={current ?? ""}>
        {current === "page:crew" ? (
          <CrewBots crew={crew} onEdit={setEditing} onChat={(bot) => setCurrent(`bot:${bot}`)} />
        ) : current === "page:routines" ? (
          <Routines crew={crew} onOpen={openTask} />
        ) : current === "page:tools" ? (
          <Tools crew={crew} />
        ) : current === "page:analytics" ? (
          <Analytics crew={crew} />
        ) : current === "page:settings" ? (
          <Settings crew={crew} />
        ) : current === "review" ? (
          <CodeReview crew={crew} onSettings={() => setCurrent("page:settings")} />
        ) : chat?.kind === "direct" ? (
          <DirectChat crew={crew} chat={chat} onOpen={openTask} profileOpen={Boolean(profile)} onProfile={() => setProfileOn(!profileOn)} />
        ) : chat?.kind === "group" ? (
          <GroupChat crew={crew} chat={chat} onOpen={openTask} panelOpen={Boolean(room)} onPanel={() => setProfileOn(!profileOn)} />
        ) : (
          <div className="thread" />
        )}
        </Boundary>
      </main>
      {thread && (
        <Boundary where="the thread" as="aside" className="panel" key={thread.id}>
        <Panel
          crew={crew}
          task={thread}
          onOpen={openTask}
          onClose={() => setThread(null)}
          edge={<Resizer label="Resize the thread" edge="left" width={panelWidth} min={320} max={960} cssVar="--panel-user" host={host} pane={part(".panel")} flexible={part(".main")} onChange={setPanelWidth} />}
        />
        </Boundary>
      )}
      {profile && (
        <Boundary where="the profile" as="aside" className="panel profile" key={profile}>
        <Profile
          crew={crew}
          bot={profile}
          onOpen={openTask}
          onClose={() => setProfileOn(false)}
          onEdit={() => setEditing(profile)}
          edge={<Resizer label="Resize the profile" edge="left" width={profileWidth} min={260} max={560} cssVar="--profile-user" host={host} pane={part(".panel")} flexible={part(".main")} onChange={setProfileWidth} />}
        />
        </Boundary>
      )}
      {room && (
        <Boundary where="the group's panel" as="aside" className="panel profile" key={room.id}>
        <RoomProfile
          crew={crew}
          room={room}
          onOpen={openTask}
          onChat={(bot) => setCurrent(`bot:${bot}`)}
          onClose={() => setProfileOn(false)}
          edge={<Resizer label="Resize the group's panel" edge="left" width={profileWidth} min={260} max={560} cssVar="--profile-user" host={host} pane={part(".panel")} flexible={part(".main")} onChange={setProfileWidth} />}
        />
        </Boundary>
      )}
      {editing !== null && <BotEditor crew={crew} name={editing || null} onClose={() => setEditing(null)} />}
      {newGroup && (
        <NewGroup
          crew={crew}
          onClose={() => setNewGroup(false)}
          onCreated={(id) => {
            setNewGroup(false);
            setCurrent(`room:${id}`);
            setThread(null);
          }}
        />
      )}
      <CrewBanner crew={crew} />
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
