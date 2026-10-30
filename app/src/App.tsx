import { useEffect, useRef, useState } from "react";
import { DirectChat, GroupChat } from "./components/ChatView";
import { Boundary } from "./components/Boundary";
import { CrewBanner } from "./components/CrewBanner";
import { Icon } from "./components/Icon";
import { PagePanel } from "./components/PagePanel";
import { Panel } from "./components/Panel";
import { Palette } from "./components/Palette";
import { UpdatesDialog } from "./components/UpdatesDialog";
import { paletteItems } from "./components/paletteItems";
import { showPanel } from "./components/Composer";
import { Profile, RoomProfile } from "./components/Profile";
import { RailEdge, Resizer, useWidth } from "./components/Resizer";
import { Sidebar } from "./components/Sidebar";
import { CodeReview } from "./pages/CodeReview";
import { BotEditor, CrewBots } from "./pages/CrewBots";
import { Projects } from "./pages/Projects";
import { Routines } from "./pages/Routines";
import { Settings } from "./pages/Settings";
import { Activity } from "./pages/Activity";
import { Analytics } from "./pages/Analytics";
import { Tools } from "./pages/Tools";
import { Landed } from "./pages/Landed";
import { bridge } from "./api";
import { chatToOpen, latestChat } from "./sidebar";
import { profileVisible, toggleProfile } from "./panelState";
import type { Crew } from "./state";
import { useCrew } from "./state";
import { settle } from "./components/ui";
import type { ConnectionStatus, Task } from "./types";
import "./styles/dialogs.css";

const LAST_CHAT = "crewbots.chat"; // the chat you had open, opened again at start

/** The chat you had open when the app last ran, if this browser profile kept it. */
function lastChat(): string | null {
  try {
    return localStorage.getItem(LAST_CHAT);
  } catch {
    return null;
  }
}

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
  const [page_, setPage] = useState<{ bot: string; page: string } | null>(null); // one of a bot's pages, open beside its chat
  // The profile beside a bot's chat, or a group's beside its chat: on unless you turn it off, and remembered, wherever
  // you go in between.
  const [profileOn, setProfileOn] = useRemembered("crewbots.profile", true);
  const [newGroup, setNewGroup] = useState<{ title: string; members: string[] } | null>(null); // the dialog, and what it starts with
  const [palette, setPalette] = useState(false);
  const [updates, setUpdates] = useState(false); // crewbots → Check for Updates…
  const [pull, setPull] = useState<{ repo: string; number: number } | null>(null); // one the palette went to
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
    setThread(task.kind === "thread" || task.kind === "ask" ? id : null);
    setPage(null);
  };
  const openPage = (bot: string, page: string) => {
    setThread(null);
    setPage({ bot, page });
  };

  // ⌘K: the command palette, from anywhere.
  useEffect(() => {
    const keys = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, []);
  // The crewbots menu in the menu bar.
  useEffect(
    () =>
      bridge.onMenu((item) => {
        if (item === "check-updates") setUpdates(true);
        else if (item === "settings") setCurrent("page:settings");
      }),
    [],
  );
  const go = {
    chat: (key: string) => {
      setCurrent(key);
      setThread(null);
      setPage(null);
    },
    task: (id: string) => openTask(id),
    pull: (repo: string, number: number) => {
      setPull({ repo, number });
      setCurrent("review");
    },
    newGroup: () => setNewGroup({ title: "", members: [] }),
    editBot: (name: string) => setEditing(name),
    panel: (name: string) => setTimeout(() => showPanel(name), 0), // once the palette has closed and focus is back
    sidebar: () => setSideHidden(!sideHidden),
    updates: () => setUpdates(true),
  };

  // Open where you were: the chat you had open (or the task asked for in the URL), else your first pinned one.
  useEffect(() => {
    if (current || crew.chats.length === 0) return;
    if (initial && crew.tasks.has(initial)) openTask(initial);
    else setCurrent(chatToOpen(crew.chats, lastChat()));
  }, [crew.chats.length, crew.tasks.size]);
  useEffect(() => {
    if (!current?.startsWith("bot:") && !current?.startsWith("room:")) return;
    try {
      localStorage.setItem(LAST_CHAT, current);
    } catch {
      // not remembered: the next start opens your first pinned chat
    }
  }, [current]);

  if (crew.status.state !== "connected" && !crew.connectedOnce) return <Connecting status={crew.status} />;
  const chat = crew.chats.find((c) => c.key === current);
  const page = page_ && chat?.kind === "direct" && chat.bot === page_.bot ? page_ : null; // only beside its own bot's chat
  const thread = thread_ && !page ? crew.tasks.get(thread_) : undefined;
  const showProfile = profileVisible(profileOn, Boolean(thread), Boolean(page));
  const profile = showProfile && chat?.kind === "direct" ? chat.bot : null;
  const room = showProfile && chat?.kind === "group" ? chat.room : null;
  const onProfile = () => {
    const next = toggleProfile(profileOn, Boolean(thread || page));
    setProfileOn(next.profileOn);
    setThread(next.thread);
    setPage(next.page);
  };

  return (
    <div ref={shell} className={`app ${thread || page || profile || room ? "with-panel" : ""} ${profile || room ? "with-profile" : ""} ${current === "review" ? "reviewing" : ""} ${sideHidden ? "side-hidden" : ""}`}>
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
          setPage(null);
        }}
        onOpen={openTask}
        onNewGroup={() => setNewGroup({ title: "", members: [] })}
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
        ) : current === "page:activity" ? (
          <Activity crew={crew} onOpen={openTask} />
        ) : current === "page:landed" ? (
          <Landed crew={crew} onOpen={openTask} />
        ) : current === "page:routines" ? (
          <Routines crew={crew} onOpen={openTask} />
        ) : current === "page:projects" ? (
          <Projects crew={crew} onChat={(bot) => setCurrent(`bot:${bot}`)} />
        ) : current === "page:tools" ? (
          <Tools crew={crew} />
        ) : current === "page:analytics" ? (
          <Analytics crew={crew} />
        ) : current === "page:settings" ? (
          <Settings crew={crew} />
        ) : current === "review" ? (
          <CodeReview crew={crew} onSettings={() => setCurrent("page:settings")} pick={pull} />
        ) : chat?.kind === "direct" ? (
          <DirectChat
            crew={crew}
            chat={chat}
            onOpen={openTask}
            onPage={(p) => openPage(chat.bot!, p)}
            onCustomize={() => setEditing(chat.bot!)}
            profileOpen={Boolean(profile)}
            onProfile={onProfile}
            onNewGroup={(title, members) => setNewGroup({ title, members })}
          />
        ) : chat?.kind === "group" ? (
          <GroupChat crew={crew} chat={chat} onOpen={openTask} panelOpen={Boolean(room)} onPanel={onProfile} />
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
      {page && (
        <Boundary where="the page" as="aside" className="panel" key={page.bot}>
        <PagePanel
          crew={crew}
          bot={page.bot}
          page={page.page}
          onPage={(p) => setPage({ bot: page.bot, page: p })}
          onOpen={openTask}
          onClose={() => setPage(null)}
          edge={<Resizer label="Resize the page" edge="left" width={panelWidth} min={320} max={960} cssVar="--panel-user" host={host} pane={part(".panel")} flexible={part(".main")} onChange={setPanelWidth} />}
        />
        </Boundary>
      )}
      {profile && (
        <Boundary where="the profile" as="aside" className="panel profile" key={profile}>
        <Profile
          crew={crew}
          bot={profile}
          onOpen={openTask}
          onPage={(p) => openPage(profile, p)}
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
          onGone={() => setCurrent(latestChat(crew.chats.filter((c) => c.key !== current)))}
          edge={<Resizer label="Resize the group's panel" edge="left" width={profileWidth} min={260} max={560} cssVar="--profile-user" host={host} pane={part(".panel")} flexible={part(".main")} onChange={setProfileWidth} />}
        />
        </Boundary>
      )}
      {editing !== null && <BotEditor crew={crew} name={editing || null} onClose={() => setEditing(null)} />}
      {newGroup && (
        <NewGroup
          crew={crew}
          start={newGroup}
          onClose={() => setNewGroup(null)}
          onCreated={(id) => {
            setNewGroup(null);
            setCurrent(`room:${id}`);
            setThread(null);
          }}
        />
      )}
      {palette && <Palette items={paletteItems(crew, current, go)} onClose={() => setPalette(false)} />}
      {updates && <UpdatesDialog onClose={() => setUpdates(false)} restarting={crew.crewState?.restarting} />}
      <CrewBanner crew={crew} onUpdates={() => setUpdates(true)} />
      {crew.error && (
        <div className="toast" onClick={crew.clearError}>
          <Icon name="alert" size={14} />
          {crew.error}
        </div>
      )}
    </div>
  );
}

function NewGroup({ crew, start, onClose, onCreated }: { crew: Crew; start: { title: string; members: string[] }; onClose: () => void; onCreated: (roomId: string) => void }) {
  const [title, setTitle] = useState(start.title);
  const [members, setMembers] = useState<string[]>(start.members);
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
