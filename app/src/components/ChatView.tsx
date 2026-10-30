import { Fragment, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { Crew } from "../state";
import { doing, oneLine } from "../state";
import type { Chat, Room, RoomMessage } from "../types";
import { ACTIVE } from "../types";
import { Files } from "./Attachments";
import { Avatar, GroupAvatar, labelOf } from "./Avatar";
import { Boundary } from "./Boundary";
import { Markdown, MentionText, MessageMeta, PageOpener, TimeBreak, ago, fullDate, timeBreaks, useHoveredMessage } from "./bits";
import { crewCommands } from "./CommandPreview";
import { Composer } from "./Composer";
import { FindBar, Finding, count, shown, useCurrentHit, useFind } from "./Find";
import { Icon } from "./Icon";
import { Welcoming } from "./Offer";
import { Thread } from "./Thread";
import { settle, useBottomAnchor } from "./ui";
import "../styles/chat.css";

/** Your ongoing chat with one bot. Work it takes on shows as thread cards, which open beside the chat. */
export function DirectChat(props: { crew: Crew; chat: Chat; onOpen: (id: string) => void; onPage: (page: string) => void; onCustomize: () => void; onProfile: () => void; profileOpen: boolean; onNewGroup: (title: string, members: string[]) => void }) {
  const { crew, chat } = props;
  const task = chat.task_id ? crew.tasks.get(chat.task_id) : undefined;
  const bot = crew.bots.find((b) => b.name === chat.bot);
  const find = useFind();
  useMarkRead(crew, chat);
  const threads = task ? crew.family(task.id) : [];
  const busy = threads.filter((t) => ACTIVE.includes(t.status) && t.kind !== "ask");
  const asking = threads.filter((t) => ACTIVE.includes(t.status) && t.kind === "ask"); // a teammate answering, not this bot working
  const status = task && ACTIVE.includes(task.status)
    ? doing(crew.latest.get(task.id)) || "Working…"
    : busy.length
      ? `Working on ${oneLine(busy[0].title, 60)}${busy.length > 1 ? ` and ${busy.length - 1} more` : ""}`
      : asking.length
        ? `Waiting on ${labelOf(asking[0].bot)}'s answer${asking.length > 1 ? ` and ${asking.length - 1} more` : ""}`
        : chat.last ? `Active ${ago(chat.last.at)}` : bot?.description ?? "";

  return (
    <>
      <header className="chat-head" data-tauri-drag-region="deep">
        <button className="chat-head-who" onClick={props.onProfile} title="Profile">
          <Avatar name={chat.bot!} size={30} working={chat.working} />
          <span className="chat-head-text">
            <span className="chat-head-title">{labelOf(chat.bot!)}</span>
            <span className="chat-head-sub">{oneLine(status, 90)}</span>
          </span>
        </button>
        <span className="grow" />
        <button
          className="icon-button"
          aria-pressed={props.profileOpen}
          onClick={(e) => {
            settle(e);
            props.onProfile();
          }}
          title="Profile"
        >
          <Icon name="profile" size={16} />
        </button>
      </header>
      {find.finding && <FindBar find={find} placeholder={`Find in your chat with ${labelOf(chat.bot!)}`} />}
      <PageOpener.Provider value={props.onPage}>
        <Welcoming.Provider value={{ customize: props.onCustomize }}>
          {task && isEmpty(crew, task.id) ? (
            <Welcome crew={crew} bot={chat.bot!} onSay={(text) => crew.say(chat.bot!, text)} />
          ) : task ? (
            <Thread crew={crew} task={task} onOpen={props.onOpen} chat find={{ query: find.lookFor, current: find.current, onFound: find.setFound }} />
          ) : (
            <div className="thread" />
          )}
        </Welcoming.Provider>
      </PageOpener.Provider>
      <div className="dock">
        <Composer
          key={chat.key}
          placeholder={task?.status === "needs_input" ? `Answer ${labelOf(chat.bot!)}` : `Message ${labelOf(chat.bot!)}`}
          asking={task?.status === "needs_input"}
          mentions={crew.bots.map((b) => b.name)}
          onSend={(text, files) => crew.say(chat.bot!, text, files)}
          task={task?.id}
          draft={chat.key}
          autoFocus
          main
          actions={{
            who: labelOf(chat.bot!),
            new: (goal) => crew.threadFor(chat.bot!, goal),
            group: (title) => props.onNewGroup(title, [chat.bot!]),
            remember: (note) => crew.rememberFor(chat.bot!, note),
            stop: task && ACTIVE.includes(task.status) ? () => crew.cancel(task.id) : undefined,
            ...crewCommands(crew),
          }}
        />
      </div>
    </>
  );
}

/** A group chat: you and some bots. The lead answers; whoever is @mentioned joins in, and bots can bring each other in. */
export function GroupChat(props: { crew: Crew; chat: Chat; onOpen: (id: string) => void; panelOpen: boolean; onPanel: () => void }) {
  const { crew, chat } = props;
  const room = chat.room!;
  const messages = crew.roomMessages.get(room.id);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => crew.ensureRoom(room.id), [room.id, crew.ensureRoom]);
  useMarkRead(crew, chat, messages?.length);
  // A new message keeps you at the bottom if you were there; reading further up, you stay where you are.
  const stuck = useRef(true);
  useEffect(() => {
    stuck.current = true;
  }, [room.id]);
  useEffect(() => {
    if (stuck.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [room.id, messages?.length, Boolean(room.stopped)]);
  useBottomAnchor(box, Boolean(messages?.length));

  const seats = room.members.map((m) => [...crew.tasks.values()].find((t) => t.kind === "seat" && t.room_id === room.id && t.bot === m));
  // Working is a turn that is running; a seat waiting between tries is not, and says so.
  const typing = room.members.filter((_, i) => seats[i]?.status === "running" && !seats[i]?.attempt);
  const retrying = room.members.flatMap((m, i) => (seats[i]?.attempt ? [{ bot: m, ...seats[i]!.attempt! }] : []));
  const retryText = retrying.map((r) => `${labelOf(r.bot)} couldn't start, retrying (attempt ${r.attempt} of ${r.of})`).join(" · ");
  // Where time has passed, a centred day and time goes over the next message, which starts a group of its own.
  const breaks = useMemo(() => new Set([...timeBreaks(messages ?? [])].map((i) => messages![i].id)), [messages]);
  const groups = useMemo(() => byAuthor(messages ?? [], breaks), [messages, breaks]);
  const find = useFind();
  const hovered = useHoveredMessage();
  const hits = useMemo(() => {
    const out: { el: () => Element | null | undefined; nth: number }[] = [];
    for (const m of find.lookFor ? messages ?? [] : []) {
      const text = m.author === "user" ? m.text : shown(m.text);
      for (let nth = 0, n = count(text, find.lookFor); nth < n; nth++) out.push({ el: () => box.current?.querySelector(`[data-msg="${m.id}"]`), nth });
    }
    return out;
  }, [messages, find.lookFor]);
  useEffect(() => find.setFound(hits.length), [hits.length]);
  useCurrentHit(box, hits, find.current, find.lookFor);

  return (
    <>
      <header className="chat-head" data-tauri-drag-region="deep">
        <button className="chat-head-who" onClick={props.onPanel} title="About this group">
          <GroupAvatar members={room.members} size={30} />
          <span className="chat-head-text">
            <span className="chat-head-title">{room.title}</span>
            <span className="chat-head-sub">
              {retrying.length > 0 ? retryText : typing.length > 0 ? `${typing.map(labelOf).join(", ")} ${typing.length === 1 ? "is" : "are"} working` : `${room.members.map(labelOf).join(", ")} · ${labelOf(room.lead)} leads`}
            </span>
          </span>
        </button>
        <span className="grow" />
        <button
          className="icon-button"
          aria-pressed={props.panelOpen}
          onClick={(e) => {
            settle(e);
            props.onPanel();
          }}
          title="About this group"
        >
          <Icon name="profile" size={16} />
        </button>
      </header>
      {find.finding && <FindBar find={find} placeholder={`Find in ${room.title}`} />}
      {messages?.length === 0 ? (
        <GroupWelcome crew={crew} room={room} onSay={(text) => crew.post(room.id, text)} />
      ) : (
        <div
          className="thread"
          ref={box}
          onMouseMove={hovered.onMouseMove}
          onMouseLeave={hovered.onMouseLeave}
          onScroll={(e) => {
            const el = e.currentTarget;
            stuck.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
            hovered.clear();
          }}
        >
          <Finding.Provider value={find.lookFor}>
          <div className="thread-col chat room">
            {groups.map((group) =>
              group.author === "user" ? (
                group.messages.map((m, i, all) => (
                  <Boundary key={m.id} where={`a group chat message (${m.id})`}>
                    <div
                      className={["you", breaks.has(m.id) ? "after-break" : i > 0 ? "joined" : "", i < all.length - 1 && !breaks.has(all[i + 1].id) ? "continued" : ""].filter(Boolean).join(" ")}
                      data-msg={m.id}
                    >
                      {breaks.has(m.id) && <TimeBreak at={m.at} />}
                      <div className="message-row">
                        <div className="message-body">
                          <Files items={m.attachments ?? []} mine />
                          {m.text && <div className="you-text"><MentionText text={m.text} everyone plain /></div>}
                        </div>
                        <MessageMeta at={m.at} text={m.text} />
                      </div>
                    </div>
                  </Boundary>
                ))
              ) : (
                <Fragment key={group.messages[0].id}>
                  {breaks.has(group.messages[0].id) && <TimeBreak at={group.messages[0].at} />}
                  <div className="room-group">
                    <Avatar name={group.author} size={28} />
                    <div className="room-group-body">
                      <div className="room-author">
                        {labelOf(group.author)}
                        <span className="faint" title={fullDate(group.messages[0].at)}> · {ago(group.messages[0].at)}</span>
                      </div>
                      {group.messages.map((m) => (
                        <Boundary key={m.id} where={`a group chat message (${m.id})`}>
                          <div className="room-message message-row" data-msg={m.id}>
                            <div className="message-body">
                              {m.text && <RoomBubble text={m.text} onOpen={props.onOpen} />}
                              <Files items={m.attachments ?? []} />
                            </div>
                            <MessageMeta at={m.at} text={m.text} />
                          </div>
                        </Boundary>
                      ))}
                    </div>
                  </div>
                </Fragment>
              ),
            )}
            {typing.length > 0 && (
              <div className="working-indicator">
                <span className="working-dot" />
                <span className="shimmer">
                  {typing.map(labelOf).join(", ")} {typing.length === 1 ? "is" : "are"} working
                </span>
              </div>
            )}
            {retrying.length > 0 && (
              <div className="working-indicator retrying">
                <span className="working-dot" />
                <span>{retryText}</span>
              </div>
            )}
            {room.max_depth != null && room.stopped && typing.length === 0 && (
              <div className="room-stopped">
                The bots stopped after {room.stopped.after} replies.
                <button className="button small" onClick={() => crew.carryOn(room.id)} title={`Let ${room.stopped.waiting.map(labelOf).join(", ")} answer, with a fresh count`}>
                  Continue
                </button>
              </div>
            )}
          </div>
          </Finding.Provider>
        </div>
      )}
      <div className="dock">
        {room.archived_at && (
          <div className="room-archived">
            <Icon name="archive" size={14} />
            Archived. Saying something here brings it back.
            <button className="button small" onClick={() => crew.changeRoom(room.id, { archived: false })}>
              Unarchive
            </button>
          </div>
        )}
        <Composer key={chat.key} placeholder={`Message ${room.title}`} mentions={[...room.members, "everyone"]} onSend={(text, files) => crew.post(room.id, text, files)} draft={chat.key} autoFocus main actions={crewCommands(crew)} />
      </div>
    </>
  );
}

const FOLD = 8; // lines of a long bot message a group chat shows, folding it only when that hides 3 more or over

/** A bot's message in a group chat. A long one folds to its first lines, so the room reads at a glance, and opens
 * with Show more, or by itself when what you're finding is in it. A direct chat shows everything. */
export function RoomBubble({ text, onOpen }: { text: string; onOpen: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [long, setLong] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const finding = useContext(Finding);
  useEffect(() => {
    const node = body.current;
    if (!node) return;
    const measure = () => setLong(node.scrollHeight > parseFloat(getComputedStyle(node).lineHeight) * (FOLD + 3));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [text]);
  const folded = long && !open && !(finding && count(shown(text), finding));
  return (
    <div className="room-bubble">
      <div className={folded ? "room-folded" : undefined}><div ref={body}><Markdown text={text} onSelect={onOpen} everyone /></div></div>
      {long && <button type="button" className="text-button room-fold" aria-expanded={!folded} onClick={() => setOpen(folded)}>{folded ? "Show more" : "Show less"}</button>}
    </div>
  );
}

/** A group chat before its first message: who's in it, how it works, and a few ways to start. */
function GroupWelcome({ crew, room, onSay }: { crew: Crew; room: Room; onSay: (text: string) => void }) {
  const members = [room.lead, ...room.members.filter((m) => m !== room.lead)];
  const starts = ["@everyone what are you working on?", "Plan this week's work together", `@${members.find((m) => m !== room.lead) ?? room.lead} what do you need from me?`];
  return (
    <div className="welcome">
      <GroupAvatar members={room.members} size={72} />
      <h2>{room.title}</h2>
      <div className="welcome-members">
        {members.map((name) => {
          const bot = crew.bots.find((b) => b.name === name);
          return (
            <span key={name} className="welcome-member">
              <Avatar name={name} size={34} />
              <span>{labelOf(name)}</span>
              <span className="faint small">{name === room.lead ? "Leads" : bot?.display_name ? bot.title : ""}</span>
            </span>
          );
        })}
      </div>
      <p>
        {labelOf(room.lead)} answers what you say here or hands it to the right teammate, and anyone you @mention joins in (@everyone for all of them).
        Bots bring each other in the same way, and hand out work as threads.
      </p>
      <div className="welcome-starts">
        {starts.map((text) => (
          <button key={text} className="welcome-start" onClick={() => onSay(text)}>
            <MentionText text={text} everyone plain />
          </button>
        ))}
      </div>
    </div>
  );
}

function isEmpty(crew: Crew, taskId: string): boolean {
  const events = crew.events.get(taskId);
  return events !== undefined && events.length === 0;
}

/** The first time you open a bot's chat: who it is, and a few ways to start. */
function Welcome({ crew, bot, onSay }: { crew: Crew; bot: string; onSay: (text: string) => void }) {
  const card = crew.bots.find((b) => b.name === bot);
  const starts = card?.can_delegate
    ? ["What can you take off my plate?", "Plan a goal with me", "Who's on the crew, and what does each do?"]
    : ["What do you do, and how should I work with you?", "What would you need from me to start?", "What have you worked on lately?"];
  return (
    <div className="welcome">
      <Avatar name={bot} size={72} />
      <h2>{labelOf(bot)}</h2>
      {card?.display_name && <div className="faint">{card.title}</div>}
      <p>{card?.description}</p>
      <div className="welcome-starts">
        {starts.map((text) => (
          <button key={text} className="welcome-start" onClick={() => onSay(text)}>
            {text}
          </button>
        ))}
      </div>
      <p className="faint small">This chat carries on from day to day. Real work opens as threads beside it.</p>
    </div>
  );
}

function useMarkRead(crew: Crew, chat: Chat, length?: number) {
  useEffect(() => {
    if (chat.unread > 0 && document.hasFocus()) crew.read(chat.key);
  }, [chat.key, chat.unread, length]);
}

function byAuthor(messages: RoomMessage[], breaks: Set<number>): { author: string; messages: RoomMessage[] }[] {
  const groups: { author: string; messages: RoomMessage[] }[] = [];
  for (const m of messages) {
    const last = groups.at(-1);
    if (last && last.author === m.author && !breaks.has(m.id)) last.messages.push(m);
    else groups.push({ author: m.author, messages: [m] });
  }
  return groups;
}
