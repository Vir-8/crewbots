import { useState } from "react";
import type { Crew, InboxItem } from "../state";
import { oneLine } from "../state";
import type { Chat } from "../types";
import { Avatar, GroupAvatar } from "./Avatar";
import { ago } from "./bits";
import { Icon } from "./Icon";

const NEED: Record<InboxItem["reason"], string> = {
  merge: "Ready to merge",
  question: "Has a question",
  held: "Waiting on a budget",
  failed: "Failed",
  interrupted: "Interrupted",
};

/** Your conversations, like a messaging app: a chat with each bot and the group chats, most recent first. */
export function Sidebar(props: {
  crew: Crew;
  current: string | null; // a chat's key, or "settings"
  onChat: (key: string) => void;
  onOpen: (taskId: string) => void;
  onNewGroup: () => void;
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
      <div className="sidebar-drag" data-tauri-drag-region />
      <div className="side-top">
        <label className="side-search">
          <Icon name="search" size={14} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" />
        </label>
        <button className="icon-button" onClick={props.onNewGroup} title="New group chat">
          <Icon name="compose" size={15} />
        </button>
      </div>

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

      <button className={`side-row settings-row ${props.current === "settings" ? "on" : ""}`} onClick={() => props.onChat("settings")}>
        <Icon name="settings" size={15} />
        <span className="side-title">Crew settings</span>
        {crew.routines?.crew_error || Object.keys(crew.routines?.errors ?? {}).length ? <Icon name="alert" size={13} className="settings-alert" /> : null}
      </button>
    </aside>
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
          <span className="chat-row-title">{chat.kind === "group" ? chat.title : chat.title}</span>
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
