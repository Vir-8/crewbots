import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, bridge } from "./api";
import { askPlans } from "./plans";
import { setCrew } from "./components/Avatar";
import type { Bot, Chat, ConnectionStatus, CrewState, Live, Models, ReviewInbox, RoomChange, RoomMessage, Routines, StoredEvent, Task } from "./types";
import { ACTIVE } from "./types";

const RECENT = 400;
const ROUTINES_EVERY_MS = 60_000; // next and last run times move on their own
const REVIEWS_EVERY_MS = 5 * 60_000; // pull requests waiting on you, from GitHub

/** Everything a layout needs: the crew, its tasks, recent activity, and the actions. Layouts only arrange it. */
export function useCrew() {
  const [status, setStatus] = useState<ConnectionStatus>({ state: "connecting", home: "" });
  const [connectedOnce, setConnectedOnce] = useState(false);
  const [bots, setBots] = useState<Bot[]>([]);
  const [tasks, setTasks] = useState<Map<string, Task>>(new Map());
  const [events, setEvents] = useState<Map<string, StoredEvent[]>>(new Map()); // only tasks someone opened
  const [recent, setRecent] = useState<StoredEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [crewState, setCrewState] = useState<CrewState | null>(null);
  const [routines, setRoutines] = useState<Routines | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [roomMessages, setRoomMessages] = useState<Map<string, RoomMessage[]>>(new Map()); // only rooms someone opened
  const [pageStamps, setPageStamps] = useState<Map<string, number>>(new Map()); // bot → how many times its pages changed

  // The chat list's previews and unread counts come from the daemon; a burst of live updates reloads it once.
  const chatsTimer = useRef<number | undefined>(undefined);
  const loadChats = useCallback(() => api.chats().then(setChats, () => undefined), []);
  const reloadChats = useCallback(() => {
    window.clearTimeout(chatsTimer.current);
    chatsTimer.current = window.setTimeout(loadChats, 250);
  }, [loadChats]);

  const [reviewInbox, setReviewInbox] = useState<ReviewInbox | null>(null);
  const [models, setModels] = useState<Models | null>(null); // asked of the CLIs once, when something needs them
  const loadModels = useCallback(() => {
    if (!models) api.models().then(setModels, () => undefined);
  }, [models]);
  const loadReviews = useCallback((fresh = false) => api.reviewInbox(fresh).then(setReviewInbox, () => undefined), []);

  const loadRoutines = useCallback(() => api.routines().then(setRoutines, () => setRoutines(null)), []);

  const loadAll = useCallback(async () => {
    try {
      const [b, t, r, c] = await Promise.all([api.bots(), api.tasks(), api.recent(RECENT), api.crew()]);
      setCrewState(c);
      loadRoutines();
      loadChats();
      setBots(b);
      setCrew(b);
      setTasks(new Map(t.map((task) => [task.id, task])));
      setRecent(r as StoredEvent[]);
      setEvents(new Map());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [loadRoutines, loadChats]);

  useEffect(() => {
    bridge.status().then(setStatus);
    const offs = [
      bridge.onStatus(setStatus),
      bridge.onResync(loadAll),
      bridge.onLive((message: Live) => {
        if (message.type === "task") {
          setTasks((current) => new Map(current).set(message.task.id, message.task));
          reloadChats();
          return;
        }
        if (message.type === "crew") {
          setCrewState(message.crew);
          return;
        }
        if (message.type === "bots") {
          api.bots().then((b) => {
            setCrew(b);
            setBots(b);
            loadChats();
          }, () => undefined);
          return;
        }
        if (message.type === "room") {
          reloadChats();
          return;
        }
        if (message.type === "room_gone") {
          setRoomMessages((current) => {
            const next = new Map(current);
            next.delete(message.room_id);
            return next;
          });
          reloadChats();
          return;
        }
        if (message.type === "pins" || message.type === "chat_mute") {
          reloadChats();
          return;
        }
        if (message.type === "page") {
          setPageStamps((current) => new Map(current).set(message.bot, (current.get(message.bot) ?? 0) + 1));
          return;
        }
        if (message.type === "room_message") {
          const m = message.message;
          setRoomMessages((current) => {
            const known = current.get(m.room_id);
            if (!known || known.some((x) => x.id === m.id)) return current;
            return new Map(current).set(m.room_id, [...known, m]);
          });
          reloadChats();
          return;
        }
        reloadChats();
        const event = message.event;
        setRecent((current) => (current.some((e) => e.id === event.id) ? current : [...current.slice(-RECENT + 1), event]));
        setEvents((current) => {
          const known = current.get(event.task_id);
          if (!known || known.some((e) => e.id === event.id)) return current;
          return new Map(current).set(event.task_id, [...known, event]);
        });
      }),
    ];
    return () => offs.forEach((off) => off());
  }, [loadAll, reloadChats]);

  useEffect(() => {
    if (status.state === "connected") {
      setConnectedOnce(true);
      loadAll();
    }
  }, [status.state, loadAll]);

  useEffect(() => {
    if (status.state !== "connected") return;
    loadReviews();
    askPlans(); // so /usage and Analytics open with them already there
    const timers = [setInterval(loadRoutines, ROUTINES_EVERY_MS), setInterval(loadReviews, REVIEWS_EVERY_MS)];
    return () => timers.forEach(clearInterval);
  }, [status.state, loadRoutines, loadReviews]);

  const ensureEvents = useCallback(
    (taskId: string) => {
      if (events.has(taskId)) return;
      setEvents((current) => new Map(current).set(taskId, [])); // claim it, so it loads once
      api.events(taskId).then(
        (loaded) => setEvents((current) => current.has(taskId)
          ? new Map(current).set(taskId, mergeLoadedEvents(loaded as StoredEvent[], current.get(taskId)!))
          : current),
        (e) => setError(String(e)),
      );
    },
    [events],
  );

  const ensureRoom = useCallback(
    (roomId: string) => {
      if (roomMessages.has(roomId)) return;
      setRoomMessages((current) => new Map(current).set(roomId, []));
      api.roomMessages(roomId).then(
        (loaded) => setRoomMessages((current) => new Map(current).set(roomId, loaded)),
        (e) => setError(String(e)),
      );
    },
    [roomMessages],
  );

  const act = useCallback(
    <T,>(promise: Promise<T>) =>
      promise.catch((e) => {
        setError(String(e).replace(/^Error: /, ""));
        throw e;
      }),
    [],
  );

  const derived = useMemo(() => derive(tasks, recent), [tasks, recent]);

  return {
    status,
    crewState,
    routines,
    chats,
    roomMessages,
    /** Changes whenever one of the bot's pages does: what shows a page reads it again. */
    pageStamp: (bot: string) => pageStamps.get(bot) ?? 0,
    ensureRoom,
    say: (bot: string, text: string, attachments: string[] = []) => act(api.say(bot, text, attachments)),
    post: (roomId: string, text: string, attachments: string[] = []) => act(api.post(roomId, text, attachments)),
    read: (key: string) => api.read(key).then(reloadChats, () => undefined),
    carryOn: (roomId: string) => act(api.carryOn(roomId)),
    react: (id: number, reaction: "up" | "down") => act(api.react(id, reaction)),
    muteChat: (chat: Chat, muted: boolean) => (chat.kind === "group"
      ? act(api.changeRoom(chat.room!.id, { muted })) : act(api.muteChat(chat.bot!, muted))).then(() => loadChats()),
    changeRoom: (roomId: string, change: RoomChange) => act(api.changeRoom(roomId, change)),
    deleteRoom: (roomId: string) => act(api.deleteRoom(roomId)),
    threadFor: (bot: string, goal: string) => act(api.threadFor(bot, goal)),
    rememberFor: (bot: string, text: string) => act(api.rememberFor(bot, text)),
    /** Pins these chats to the top of the sidebar, in this order, at once here and then in the daemon. */
    setPins: (keys: string[]) => {
      setChats((current) => current.map((c) => ({ ...c, pinned: keys.includes(c.key) ? keys.indexOf(c.key) : null })));
      return act(api.setPins(keys));
    },
    newRoom: async (title: string, members: string[], lead?: string) => {
      const room = await act(api.newRoom(title, members, lead));
      loadChats();
      return room;
    },
    bots,
    tasks,
    events,
    recent,
    error,
    clearError: () => setError(null),
    ensureEvents,
    ...derived,
    reviewInbox,
    loadReviews,
    models,
    loadModels,
    pause: (on: boolean) => act(api.pause(on)).then(setCrewState),
    restart: (whenIdle: boolean) => act(api.restart(whenIdle)).then(setCrewState),
    cancelRestart: () => act(api.cancelRestart()).then(setCrewState),
    /** It has been connected: from then on a lost daemon keeps the window as it is while it reconnects. */
    connectedOnce,
    /** Reads the crew again after an edit: its bots, their chats and the routines. */
    refresh: async () => {
      const b = await api.bots();
      setCrew(b);
      setBots(b);
      loadChats();
      loadRoutines();
    },
    newTask: (bot: string, goal: string, workspace?: string) => act(api.newTask(bot, goal, workspace)),
    send: (taskId: string, text: string, attachments: string[] = [], questionId?: number) => act(api.send(taskId, text, attachments, questionId)),
    cancel: (taskId: string) => act(api.cancel(taskId)),
    merge: (taskId: string, force: boolean) => act(api.merge(taskId, force)),
    withdrawMerge: (taskId: string, reason: string) => act(api.withdrawMerge(taskId, reason)),
    release: (taskId: string) => act(api.release(taskId)),
    addStarter: async (name: string) => {
      await act(api.addStarter(name));
      loadRoutines();
    },
    runRoutine: async (name: string) => {
      const task = await act(api.runRoutine(name));
      loadRoutines();
      return task;
    },
    confirmRoutine: async (name: string) => {
      const task = await act(api.confirmRoutine(name));
      loadRoutines();
      return task;
    },
    deleteRoutine: async (name: string) => {
      await act(api.deleteRoutine(name));
      loadRoutines();
    },
  };
}

export type Crew = ReturnType<typeof useCrew>;

export interface InboxItem {
  task: Task;
  reason: "merge" | "permission" | "question" | "held" | "failed" | "interrupted";
  text: string;
}

/** Retain live events that arrived while the task's stored events were loading. */
export function mergeLoadedEvents(loaded: StoredEvent[], live: StoredEvent[]): StoredEvent[] {
  return [...new Map([...loaded, ...live].map((event) => [event.id, event])).values()].sort((a, b) => a.id - b.id);
}

export function derive(tasks: Map<string, Task>, recent: StoredEvent[]) {
  const all = [...tasks.values()];
  const byAge = [...all].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const children = new Map<string, Task[]>();
  for (const t of byAge) if (t.parent_id) children.set(t.parent_id, [...(children.get(t.parent_id) ?? []), t]);
  const goals = byAge.filter((t) => !t.parent_id || !tasks.has(t.parent_id)).reverse();

  const latest = new Map<string, StoredEvent>(); // the last thing each task did, for one-line status
  const verdicts = new Map<string, StoredEvent[]>(); // review events, by the work they reviewed
  for (const e of recent) {
    // A message or a new turn counts too: what a bot did before it isn't what it's doing now.
    if (["message", "turn_start", "text", "tool_call", "handoff", "review", "escalation", "merged", "merge_proposed"].includes(e.kind)) {
      latest.set(e.task_id, e);
    }
    if (e.kind === "review" && e.data.task_id === e.task_id) {
      verdicts.set(e.data.work_id, [...(verdicts.get(e.data.work_id) ?? []), e]);
    }
  }

  const questions = new Map<string, string>();
  for (const e of recent) if (e.kind === "escalation") questions.set(e.task_id, e.data.question);
  const holds = new Map<string, string>();
  for (const e of recent) if (e.kind === "held") holds.set(e.task_id, e.data.reason);
  // What a task asks the user to allow, while it's unanswered: from the asking task's own record of it.
  const asked = new Map<number, { task: string; what: string }>();
  for (const e of recent) {
    if (e.kind === "permission" && e.data.task === e.task_id) asked.set(e.data.permission, { task: e.task_id, what: e.data.what });
    else if (e.kind === "permission_answer") asked.delete(e.data.permission);
  }
  const asks = new Map([...asked.values()].map((a) => [a.task, a.what]));

  const inbox: InboxItem[] = [];
  for (const task of [...all].sort((a, b) => b.updated_at.localeCompare(a.updated_at))) {
    if (task.merge_state === "proposed") {
      inbox.push({ task, reason: "merge", text: "Reviewed and ready to merge" });
    } else if (asks.has(task.id)) {
      inbox.push({ task, reason: "permission", text: asks.get(task.id)! });
    } else if (task.status === "needs_input" && !task.parent_id) {
      inbox.push({ task, reason: "question", text: questions.get(task.id) ?? "Waiting on your answer" });
    } else if (task.status === "held") {
      inbox.push({ task, reason: "held", text: holds.get(task.id) ?? "Waiting on a budget" });
    } else if (task.status === "failed" && !task.parent_id) {
      inbox.push({ task, reason: "failed", text: task.error ?? "Failed" });
    } else if (task.status === "interrupted" && !task.parent_id) {
      inbox.push({ task, reason: "interrupted", text: "Stopped when the daemon did; send a message to resume" });
    }
  }

  const family = (id: string): Task[] => (children.get(id) ?? []).flatMap((k) => [k, ...family(k.id)]);
  const running = all.filter((t) => ACTIVE.includes(t.status));
  return { goals, children, family, latest, verdicts, inbox, running, holds, asks };
}

export { describe, doing, limitText, describeCall, oneLine, workingLine } from "./activityText";
