import { useEffect, useRef, useState } from "react";
import type { Crew, InboxItem } from "../state";
import { oneLine } from "../state";
import type { Chat } from "../types";
import { Avatar, GroupAvatar, labelOf } from "./Avatar";
import { ago } from "./bits";
import { Icon } from "./Icon";

const NEED: Record<InboxItem["reason"], string> = {
  merge: "Ready to merge",
  question: "Has a question",
  held: "Waiting on a budget",
  failed: "Failed",
  interrupted: "Interrupted",
};

const PAGES: [string, string, "users" | "repeat" | "chart" | "settings"][] = [
  ["page:crew", "Crew", "users"],
  ["page:routines", "Routines", "repeat"],
  ["page:usage", "Usage", "chart"],
  ["page:settings", "Settings", "settings"],
];

/** Your conversations, like a messaging app: a chat with each bot and the group chats, most recent first. Code review
 * sits above them; the crew's own pages are in the menu at the bottom. */
export function Sidebar(props: {
  crew: Crew;
  current: string | null; // a chat's key, "review", or a page ("page:crew", ...)
  onChat: (key: string) => void;
  onOpen: (taskId: string) => void;
  onNewGroup: () => void;
  onHide: () => void;
}) {
  const { crew } = props;
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const chats = [...crew.chats]
    .filter((c) => !q || [c.title, c.bot, c.last?.text, ...(c.room?.members ?? [])].some((s) => s?.toLowerCase().includes(q)))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const needs = crew.inbox.filter((item) => item.task.kind === "thread");

  return (
    <aside className="sidebar">
      <div className="sidebar-drag" data-tauri-drag-region>
        <button className="icon-button" onClick={props.onHide} title="Hide the sidebar">
          <Icon name="panelClose" size={15} />
        </button>
      </div>
      <div className="side-top">
        <label className="side-search">
          <Icon name="search" size={14} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" />
        </label>
        <button className="icon-button" onClick={props.onNewGroup} title="New group chat">
          <Icon name="compose" size={15} />
        </button>
      </div>

      {!q && (
        <button className={`side-row nav-row ${props.current === "review" ? "on" : ""}`} onClick={() => props.onChat("review")}>
          <Icon name="branch" size={15} />
          <span className="side-title">Code review</span>
          {(crew.reviewInbox?.direct.length ?? 0) > 0 && <span className="count">{crew.reviewInbox!.direct.length}</span>}
        </button>
      )}

      {needs.length > 0 && !q && (
        <section className="side-needs">
          {needs.map((item) => (
            <button key={item.task.id} className={`need-row ${item.reason}`} onClick={() => props.onOpen(item.task.id)}>
              <span className="need-dot" />
              <span className="side-text">
                <span className="need-title">{oneLine(item.task.title, 60)}</span>
                <span className="side-sub">
                  {item.task.bot} · {NEED[item.reason]}
                </span>
              </span>
            </button>
          ))}
        </section>
      )}

      <div className="chat-list">
        {chats.map((chat) => (
          <ChatRow key={chat.key} chat={chat} on={props.current === chat.key} onClick={() => props.onChat(chat.key)} />
        ))}
        {chats.length === 0 && <p className="side-empty">{q ? "Nothing matches." : "Connecting to your crew…"}</p>}
      </div>

      <CrewMenu crew={crew} current={props.current} onPage={props.onChat} />
    </aside>
  );
}

/** The crew's own places: who's in it, what runs on its own, what it has used, and how crewbots is set up. */
function CrewMenu({ crew, current, onPage }: { crew: Crew; current: string | null; onPage: (page: string) => void }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => box.current?.contains(e.target as Node) || setOpen(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  const problem = Boolean(crew.routines?.crew_error || Object.keys(crew.routines?.errors ?? {}).length);
  const paused = Boolean(crew.crewState?.paused);
  const here = PAGES.find(([key]) => key === current);

  return (
    <div className="crew-menu" ref={box}>
      {open && (
        <div className="menu" role="menu">
          {PAGES.map(([key, label, icon]) => (
            <button
              key={key}
              role="menuitem"
              className={`menu-item ${current === key ? "on" : ""}`}
              onClick={() => {
                onPage(key);
                setOpen(false);
              }}
            >
              <Icon name={icon} size={15} />
              {label}
              {key === "page:settings" && problem && <Icon name="alert" size={13} className="settings-alert" />}
            </button>
          ))}
          <div className="menu-sep" />
          <button role="menuitem" className="menu-item" onClick={() => crew.pause(!paused)}>
            <Icon name={paused ? "play" : "pause"} size={15} />
            {paused ? "Resume the crew" : "Pause the crew"}
          </button>
        </div>
      )}
      <button className={`side-row crew-button ${open || here ? "on" : ""}`} onClick={() => setOpen(!open)}>
        <span className="crew-faces">
          {crew.bots.slice(0, 3).map((b) => (
            <Avatar key={b.name} name={b.name} size={20} />
          ))}
        </span>
        <span className="side-title">{here ? here[1] : paused ? "Crew · paused" : "Your crew"}</span>
        {problem && <Icon name="alert" size={13} className="settings-alert" />}
        <Icon name="chevronDown" size={13} className={open ? "flip" : ""} />
      </button>
    </div>
  );
}

function ChatRow({ chat, on, onClick }: { chat: Chat; on: boolean; onClick: () => void }) {
  const preview = chat.last
    ? `${chat.kind === "group" ? `${chat.last.author === "user" ? "You" : chat.last.author}: ` : chat.last.author === "user" ? "You: " : ""}${oneLine(chat.last.text, 90)}`
    : chat.kind === "group"
      ? chat.room!.members.join(", ")
      : "Say hello";
  return (
    <button className={`chat-row ${on ? "on" : ""}`} onClick={onClick}>
      {chat.kind === "group" ? <GroupAvatar members={chat.room!.members} size={38} /> : <Avatar name={chat.bot!} size={38} working={chat.working} />}
      <span className="chat-row-text">
        <span className="chat-row-top">
          <span className="chat-row-title">{chat.kind === "group" ? chat.title : labelOf(chat.bot!)}</span>
          {chat.last && <span className="chat-row-time">{ago(chat.last.at, true)}</span>}
        </span>
        <span className="chat-row-bottom">
          <span className={`chat-row-preview ${chat.working ? "working" : ""}`}>{chat.working && !chat.unread ? "Working…" : preview}</span>
          {chat.unread > 0 && !on && <span className="unread-dot" />}
        </span>
      </span>
    </button>
  );
}
