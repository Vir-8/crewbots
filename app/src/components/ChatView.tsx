import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import type { Crew } from "../state";
import { doing, oneLine } from "../state";
import type { Chat, Room, RoomMessage } from "../types";
import { ACTIVE } from "../types";
import { Files } from "./Attachments";
import { Avatar, GroupAvatar, labelOf } from "./Avatar";
import { Boundary } from "./Boundary";
import { BringIn } from "./BringIn";
import { Markdown, MessageMeta, PageOpener, TimeBreak, ago, fullDate, timeBreaks } from "./bits";
import { crewCommands } from "./CommandPreview";
import { Composer } from "./Composer";
import { FindBar, Finding, Marked, count, shown, useCurrentHit, useFind } from "./Find";
import { Icon } from "./Icon";
import { Thread } from "./Thread";
import { settle } from "./ui";
import "../styles/chat.css";

/** Your ongoing chat with one bot. Work it takes on shows as thread cards, which open beside the chat. */
export function DirectChat(props: { crew: Crew; chat: Chat; onOpen: (id: string) => void; onPage: (page: string) => void; onCustomize: () => void; onProfile: () => void; profileOpen: boolean; onNewGroup: (title: string, members: string[]) => void }) {
  const { crew, chat } = props;
  const task = chat.task_id ? crew.tasks.get(chat.task_id) : undefined;
  const bot = crew.bots.find((b) => b.name === chat.bot);
  const find = useFind();
  useMarkRead(crew, chat);
  // The chief's first word, until the crew's been started off (or you said not now).
  const [started, setStarted] = useState(true);
  useEffect(() => {
    if (bot?.can_delegate) api.projects().then((p) => setStarted(p.started), () => undefined);
  }, [bot?.name, bot?.can_delegate]);
  const first = !started && bot?.can_delegate ? <FirstMeeting crew={crew} bot={bot.name} onCustomize={props.onCustomize} onStarted={() => setStarted(true)} /> : null;
  useEffect(() => {
    if (task) crew.ensureEvents(task.id); // the conversation, or nothing yet: either way it's known
  }, [task?.id, crew.ensureEvents]);

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
          className={`icon-button ${props.profileOpen ? "on" : ""}`}
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
        {(task && isEmpty(crew, task.id)) || (!task && first) ? (
          <div className="thread">
            <Welcome crew={crew} bot={chat.bot!} onSay={(text) => crew.say(chat.bot!, text)} />
            {first && <div className="thread-col chat">{first}</div>}
          </div>
        ) : task ? (
          <Thread crew={crew} task={task} onOpen={props.onOpen} chat find={{ query: find.lookFor, current: find.current, onFound: find.setFound }} after={first} />
        ) : (
          <div className="thread" />
        )}
      </PageOpener.Provider>
      <div className="dock">
        <Composer
          key={chat.key}
          placeholder={task?.status === "needs_input" ? `Answer ${labelOf(chat.bot!)}` : `Message ${labelOf(chat.bot!)}`}
          asking={task?.status === "needs_input"}
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
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [messages?.length, Boolean(room.stopped)]);

  const typing = room.members.filter((m) => {
    const seat = [...crew.tasks.values()].find((t) => t.kind === "seat" && t.room_id === room.id && t.bot === m);
    return seat && ACTIVE.includes(seat.status);
  });
  // Where time has passed, a centred day and time goes over the next message, which starts a group of its own.
  const breaks = useMemo(() => new Set([...timeBreaks(messages ?? [])].map((i) => messages![i].id)), [messages]);
  const groups = useMemo(() => byAuthor(messages ?? [], breaks), [messages, breaks]);
  const find = useFind();
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
              {typing.length > 0 ? `${typing.map(labelOf).join(", ")} ${typing.length === 1 ? "is" : "are"} replying` : `${room.members.map(labelOf).join(", ")} · ${labelOf(room.lead)} leads`}
            </span>
          </span>
        </button>
        <span className="grow" />
        <button
          className={`icon-button ${props.panelOpen ? "on" : ""}`}
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
        <div className="thread" ref={box}>
          <Finding.Provider value={find.lookFor}>
          <div className="thread-col chat room">
            {groups.map((group) =>
              group.author === "user" ? (
                group.messages.map((m) => (
                  <Boundary key={m.id} where={`a group chat message (${m.id})`}>
                    <div className="you" data-msg={m.id}>
                      {breaks.has(m.id) && <TimeBreak at={m.at} />}
                      {m.text && <div className="you-text">{withMentions(m.text)}</div>}
                      <Files items={m.attachments ?? []} mine />
                      <MessageMeta at={m.at} text={m.text} />
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
                          <div className="room-message" data-msg={m.id}>
                            {m.text && (
                              <div className="room-bubble">
                                <Markdown text={m.text} onSelect={props.onOpen} />
                              </div>
                            )}
                            <Files items={m.attachments ?? []} />
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
              <div className="working">
                <span className="working-dot" />
                <span className="shimmer">
                  {typing.map(labelOf).join(", ")} {typing.length === 1 ? "is" : "are"} replying
                </span>
              </div>
            )}
            {room.stopped && typing.length === 0 && (
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
            {withMentions(text)}
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

/** The chief's first word, like the rest of its chat but the app's own rather than a message: who it is, that it keeps
 * things moving while you're away, and an offer to start from what Claude Code and Codex already know. Gone once the
 * crew's been started off, or you've said not now. */
function FirstMeeting({ crew, bot, onCustomize, onStarted }: { crew: Crew; bot: string; onCustomize: () => void; onStarted: () => void }) {
  const card = crew.bots.find((b) => b.name === bot);
  const [bringing, setBringing] = useState(false);
  const named = Boolean(card?.display_name);
  return (
    <div className="room-group first-meeting">
      <Avatar name={bot} size={28} />
      <div className="room-group-body">
        <div className="room-bubble">Hey! I'm {named ? labelOf(bot) : `your ${(card?.title ?? "chief of staff").toLowerCase()}`}.</div>
        <div className="room-bubble">Message me anytime. I'll keep things moving, even when we're not talking, and check in with updates or questions.</div>
        {!named && <div className="room-bubble">Want to give me a name?</div>}
        <button className="first-meeting-card" onClick={onCustomize}>
          <Icon name="profile" size={15} />
          Customize {named ? labelOf(bot) : "me"}
        </button>
        <div className="room-bubble">
          To get started, I can bring in what Claude Code and Codex already know: the folders you've worked in, as projects, and what they
          remember about you and your work.
        </div>
        <div className="first-meeting-actions">
          <button className="button small primary" onClick={() => setBringing(true)}>
            Bring in context
          </button>
          <button className="button small" onClick={() => api.started().then(onStarted, onStarted)}>
            Not now
          </button>
        </div>
      </div>
      {bringing && <BringIn chief={labelOf(bot)} onClose={() => setBringing(false)} onDone={() => onStarted()} />}
    </div>
  );
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

function withMentions(text: string) {
  return text.split(/(@[\w-]+)/g).map((part, i) => (part.startsWith("@") ? <span key={i} className="mention"><Marked text={part} /></span> : <Marked key={i} text={part} />));
}
