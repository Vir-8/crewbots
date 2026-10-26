import { type ReactNode, useEffect, useRef, useState } from "react";
import type { Crew, InboxItem } from "../state";
import { archivedApart, orderChats } from "../sidebar";
import { oneLine } from "../state";
import type { Chat } from "../types";
import { Avatar, GroupAvatar, labelOf } from "./Avatar";
import { ago } from "./bits";
import { Icon } from "./Icon";
import "../styles/sidebar.css";

const NEED: Record<InboxItem["reason"], string> = {
  merge: "Ready to merge",
  question: "Has a question",
  held: "Waiting on a budget",
  failed: "Failed",
  interrupted: "Interrupted",
};

const NEED_ICON: Record<InboxItem["reason"], "merge" | "question" | "pause" | "alert"> = {
  merge: "merge",
  question: "question",
  held: "pause",
  failed: "alert",
  interrupted: "alert",
};

const PAGES: [string, string, "users" | "repeat" | "chart" | "settings" | "tool" | "handoff"][] = [
  ["page:crew", "Crew", "users"],
  ["page:activity", "Activity", "handoff"],
  ["page:tools", "Tools", "tool"],
  ["page:routines", "Routines", "repeat"],
  ["page:analytics", "Analytics", "chart"],
  ["page:settings", "Settings", "settings"],
];

/** Your conversations, like a messaging app: a chat with each bot and the group chats, most recent first. Code review
 * sits above them; the crew's own pages are in the menu at the bottom. Collapsed, it's a rail of the same things as
 * icons and faces. */
export function Sidebar(props: {
  crew: Crew;
  current: string | null; // a chat's key, "review", or a page ("page:crew", ...)
  rail: boolean;
  onChat: (key: string) => void;
  onOpen: (taskId: string) => void;
  onNewGroup: () => void;
  onExpand: () => void;
  edge?: ReactNode; // the handle on its border, to resize it, or open it from the rail
}) {
  const { crew } = props;
  const [query, setQuery] = useState("");
  // Search from the rail opens the sidebar, then puts you in the box.
  const search = useRef<HTMLInputElement>(null);
  const [searching, setSearching] = useState(false);
  useEffect(() => {
    if (props.rail || !searching) return;
    search.current?.focus();
    setSearching(false);
  }, [props.rail, searching]);
  const q = query.trim().toLowerCase();
  const [chats, archived] = archivedApart(orderChats(crew.chats, query, props.rail), props.rail ? "" : query);
  const [showArchived, setShowArchived] = useState(false);
  const needs = crew.inbox.filter((item) => item.task.kind === "thread");
  const waiting = crew.reviewInbox?.assigned?.length ?? 0; // assigned to you; review requests are in the list itself

  if (props.rail) {
    return (
      <aside className="sidebar rail">
        {props.edge}
        <div className="sidebar-drag" data-tauri-drag-region="deep" />
        <button
          className="rail-button"
          title="Search"
          onClick={() => {
            setSearching(true);
            props.onExpand();
          }}
        >
          <Icon name="search" size={15} />
        </button>
        <button className="rail-button" onClick={props.onNewGroup} title="New group chat">
          <Icon name="compose" size={15} />
        </button>
        <button className={`rail-button ${props.current === "review" ? "on" : ""}`} onClick={() => props.onChat("review")} title={waiting ? `Code review · ${waiting} assigned to you` : "Code review"}>
          <Icon name="branch" size={15} />
          {waiting > 0 && <span className="rail-count">{waiting}</span>}
        </button>
        {needs.map((item) => (
          <button key={item.task.id} className={`rail-button rail-need ${item.reason}`} onClick={() => props.onOpen(item.task.id)} title={`${oneLine(item.task.title, 60)} · ${item.task.bot} · ${NEED[item.reason]}`}>
            <Icon name={NEED_ICON[item.reason]} size={15} />
          </button>
        ))}
        <div className="rail-sep" />
        <div className="chat-list rail-chats">
          {chats.map((chat) => (
            <button key={chat.key} className={`rail-chat ${props.current === chat.key ? "on" : ""}`} onClick={() => props.onChat(chat.key)} title={chat.kind === "group" ? chat.title : labelOf(chat.bot!)}>
              {chat.kind === "group" ? <GroupAvatar members={chat.room!.members} size={34} /> : <Avatar name={chat.bot!} size={34} working={chat.working} />}
              {chat.unread > 0 && props.current !== chat.key && <span className="unread-dot" />}
            </button>
          ))}
        </div>
        <CrewMenu crew={crew} current={props.current} onPage={props.onChat} onNewGroup={props.onNewGroup} rail />
      </aside>
    );
  }

  return (
    <aside className="sidebar">
      {props.edge}
      <div className="sidebar-drag" data-tauri-drag-region="deep" />
      <div className="side-top">
        <label className="side-search">
          <Icon name="search" size={14} />
          <input ref={search} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" />
        </label>
        <button className="icon-button" onClick={props.onNewGroup} title="New group chat">
          <Icon name="compose" size={15} />
        </button>
      </div>

      {!q && (
        <button className={`side-row nav-row ${props.current === "review" ? "on" : ""}`} onClick={() => props.onChat("review")}>
          <Icon name="branch" size={15} />
          <span className="side-title">Code review</span>
          {waiting > 0 && <span className="count">{waiting}</span>}
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
        {archived.length > 0 && (
          <button className="archived-toggle" onClick={() => setShowArchived(!showArchived)} aria-expanded={showArchived}>
            <Icon name="archive" size={14} />
            Archived
            <span className="faint">{archived.length}</span>
            <Icon name="chevronDown" size={12} className={showArchived ? "flip" : ""} />
          </button>
        )}
        {showArchived && archived.map((chat) => <ChatRow key={chat.key} chat={chat} on={props.current === chat.key} onClick={() => props.onChat(chat.key)} />)}
      </div>

      <CrewMenu crew={crew} current={props.current} onPage={props.onChat} onNewGroup={props.onNewGroup} />
    </aside>
  );
}

/** The crew's own places: who's in it, what runs on its own, what it has used, and how crewbots is set up. */
function CrewMenu({ crew, current, onPage, onNewGroup, rail }: { crew: Crew; current: string | null; onPage: (page: string) => void; onNewGroup: () => void; rail?: boolean }) {
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
          <button
            role="menuitem"
            className="menu-item"
            onClick={() => {
              onNewGroup();
              setOpen(false);
            }}
          >
            <Icon name="compose" size={15} />
            New group chat
          </button>
          <div className="menu-sep" />
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
      {rail ? (
        <button className={`rail-button crew-button ${open || here ? "on" : ""}`} onClick={() => setOpen(!open)} title={paused ? "Your crew · paused" : "Your crew"}>
          <Icon name={paused ? "pause" : "users"} size={15} />
          {problem && <span className="rail-alert" />}
        </button>
      ) : (
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
      )}
    </div>
  );
}

function ChatRow({ chat, on, onClick }: { chat: Chat; on: boolean; onClick: () => void }) {
  const preview = chat.last
    ? `${chat.kind === "group" ? `${chat.last.author === "user" ? "You" : labelOf(chat.last.author)}: ` : chat.last.author === "user" ? "You: " : ""}${oneLine(chat.last.text, 90)}`
    : chat.kind === "group"
      ? chat.room!.members.map(labelOf).join(", ")
      : "Say hello";
  return (
    <button className={`chat-row ${on ? "on" : ""}`} onClick={onClick}>
      {chat.kind === "group" ? <GroupAvatar members={chat.room!.members} size={38} /> : <Avatar name={chat.bot!} size={38} working={chat.working} />}
      <span className="chat-row-text">
        <span className="chat-row-top">
          <span className="chat-row-title">{chat.kind === "group" ? chat.title : labelOf(chat.bot!)}</span>
          {chat.room?.muted && <Icon name="bellOff" size={12} className="chat-row-muted" />}
          {chat.last && <span className="chat-row-time">{ago(chat.last.at, true)}</span>}
        </span>
        <span className="chat-row-bottom">
          <span className={`chat-row-preview ${chat.working ? "working" : ""}`}>{chat.working && !chat.unread ? "Working…" : preview}</span>
          {chat.unread > 0 && !on && <span className={`unread-dot ${chat.room?.muted ? "muted" : ""}`} />}
        </span>
      </span>
    </button>
  );
}
