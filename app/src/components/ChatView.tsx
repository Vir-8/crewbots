import { useEffect, useMemo, useRef } from "react";
import type { Crew } from "../state";
import { doing, oneLine } from "../state";
import type { Chat, RoomMessage } from "../types";
import { ACTIVE } from "../types";
import { Avatar, GroupAvatar, labelOf } from "./Avatar";
import { Markdown, ago } from "./bits";
import { Composer } from "./Composer";
import { Icon } from "./Icon";
import { Thread } from "./Thread";

/** Your ongoing chat with one bot. Work it takes on shows as thread cards, which open beside the chat. */
export function DirectChat(props: { crew: Crew; chat: Chat; onOpen: (id: string) => void; onProfile: () => void; profileOpen: boolean }) {
  const { crew, chat } = props;
  const task = chat.task_id ? crew.tasks.get(chat.task_id) : undefined;
  const bot = crew.bots.find((b) => b.name === chat.bot);
  useMarkRead(crew, chat);

  const threads = task ? crew.family(task.id) : [];
  const busy = threads.filter((t) => ACTIVE.includes(t.status));
  const status = task && ACTIVE.includes(task.status)
    ? doing(crew.latest.get(task.id)) || "Working…"
    : busy.length
      ? `Working on ${oneLine(busy[0].title, 60)}${busy.length > 1 ? ` and ${busy.length - 1} more` : ""}`
      : chat.last ? `Active ${ago(chat.last.at)}` : bot?.description ?? "";

  return (
    <>
      <header className="chat-head" data-tauri-drag-region>
        <button className="chat-head-who" onClick={props.onProfile} title="Profile">
          <Avatar name={chat.bot!} size={30} working={chat.working} />
          <span className="chat-head-text">
            <span className="chat-head-title">{labelOf(chat.bot!)}</span>
            <span className="chat-head-sub">{oneLine(status, 90)}</span>
          </span>
        </button>
        <span className="grow" data-tauri-drag-region />
        <button className={`icon-button ${props.profileOpen ? "on" : ""}`} onClick={props.onProfile} title="Profile">
          <Icon name="profile" size={16} />
        </button>
      </header>
      {task ? <Thread crew={crew} task={task} onOpen={props.onOpen} chat /> : <div className="thread" />}
      <div className="dock">
        <Composer
          key={chat.key}
          placeholder={task?.status === "needs_input" ? `Answer ${labelOf(chat.bot!)}` : `Message ${labelOf(chat.bot!)}`}
          asking={task?.status === "needs_input"}
          onSend={(text) => crew.say(chat.bot!, text)}
          autoFocus
        />
      </div>
    </>
  );
}

/** A group chat: you and some bots. The lead answers; whoever is @mentioned joins in, and bots can bring each other in. */
export function GroupChat(props: { crew: Crew; chat: Chat; onOpen: (id: string) => void }) {
  const { crew, chat } = props;
  const room = chat.room!;
  const messages = crew.roomMessages.get(room.id);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => crew.ensureRoom(room.id), [room.id, crew.ensureRoom]);
  useMarkRead(crew, chat, messages?.length);
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [messages?.length]);

  const typing = room.members.filter((m) => {
    const seat = [...crew.tasks.values()].find((t) => t.kind === "seat" && t.room_id === room.id && t.bot === m);
    return seat && ACTIVE.includes(seat.status);
  });
  const groups = useMemo(() => byAuthor(messages ?? []), [messages]);

  return (
    <>
      <header className="chat-head" data-tauri-drag-region>
        <span className="chat-head-who">
          <GroupAvatar members={room.members} size={30} />
          <span className="chat-head-text">
            <span className="chat-head-title">{room.title}</span>
            <span className="chat-head-sub">
              {room.members.map(labelOf).join(", ")} · {labelOf(room.lead)} leads
            </span>
          </span>
        </span>
      </header>
      <div className="thread" ref={box}>
        <div className="thread-col chat room">
          {messages?.length === 0 && (
            <p className="room-empty faint">
              {room.lead} answers here, and anyone you @mention joins in. Bots can bring each other in the same way.
            </p>
          )}
          {groups.map((group) =>
            group.author === "user" ? (
              group.messages.map((m) => (
                <div key={m.id} className="you">
                  <div className="you-text">{withMentions(m.text)}</div>
                </div>
              ))
            ) : (
              <div key={group.messages[0].id} className="room-group">
                <Avatar name={group.author} size={28} />
                <div className="room-group-body">
                  <div className="room-author">
                    {labelOf(group.author)}
                    <span className="faint"> · {ago(group.messages[0].at)}</span>
                  </div>
                  {group.messages.map((m) => (
                    <div key={m.id} className="room-bubble">
                      <Markdown text={m.text} onSelect={props.onOpen} />
                    </div>
                  ))}
                </div>
              </div>
            ),
          )}
          {typing.length > 0 && (
            <div className="working">
              <span className="working-dot" />
              <span className="shimmer">
                {typing.map(labelOf).join(", ")} {typing.length === 1 ? "is" : "are"} replying
              </span>
            </div>
          )}
        </div>
      </div>
      <div className="dock">
        <Composer key={chat.key} placeholder={`Message ${room.title}`} mentions={room.members} onSend={(text) => crew.post(room.id, text)} autoFocus />
      </div>
    </>
  );
}

function useMarkRead(crew: Crew, chat: Chat, length?: number) {
  useEffect(() => {
    if (chat.unread > 0 && document.hasFocus()) crew.read(chat.key);
  }, [chat.key, chat.unread, length]);
}

function byAuthor(messages: RoomMessage[]): { author: string; messages: RoomMessage[] }[] {
  const groups: { author: string; messages: RoomMessage[] }[] = [];
  for (const m of messages) {
    const last = groups.at(-1);
    if (last && last.author === m.author) last.messages.push(m);
    else groups.push({ author: m.author, messages: [m] });
  }
  return groups;
}

function withMentions(text: string) {
  return text.split(/(@[\w-]+)/g).map((part, i) => (part.startsWith("@") ? <span key={i} className="mention">{part}</span> : part));
}
