// What the command palette offers: every chat and group, thread, pull request and page, and the app's commands.

import type { Crew } from "../state";
import { movePin, pinsOf } from "../sidebar";
import { oneLine } from "../state";
import type { Item } from "./Palette";
import { labelOf } from "./Avatar";

export type Go = {
  chat: (key: string) => void;
  task: (id: string) => void;
  pull: (repo: string, number: number) => void;
  newGroup: () => void;
  editBot: (name: string) => void;
  panel: (name: "usage" | "status") => void; // above the open chat's message box
  sidebar: () => void;
  updates: () => void;
};

const PAGES: [string, string, Item["icon"]][] = [
  ["review", "Code review", "branch"],
  ["page:crew", "Crew", "users"],
  ["page:activity", "Activity", "handoff"],
  ["page:tools", "Tools", "tool"],
  ["page:routines", "Routines", "repeat"],
  ["page:analytics", "Analytics", "chart"],
  ["page:settings", "Settings", "settings"],
];

export function paletteItems(crew: Crew, current: string | null, go: Go): Item[] {
  const items: Item[] = [];
  for (const chat of crew.chats) {
    const bot = chat.bot ? crew.bots.find((b) => b.name === chat.bot) : undefined;
    items.push(chat.kind === "direct"
      ? { key: chat.key, title: labelOf(chat.bot!), sub: bot?.title, at: chat.updated_at, icon: "message", group: "Chat", run: () => go.chat(chat.key) }
      : { key: chat.key, title: chat.title, sub: chat.room?.members.map(labelOf).join(", "), at: chat.updated_at, icon: "users", group: "Group chat", run: () => go.chat(chat.key) });
  }
  for (const task of crew.tasks.values()) {
    if (task.kind !== "thread" || task.review_of) continue; // a review shows in the card of the work it reviews
    items.push({ key: `task:${task.id}`, title: oneLine(task.title, 90), sub: `${labelOf(task.bot)} · ${task.status.replace("_", " ")}`, at: task.updated_at, icon: "handoff", group: "Thread", run: () => go.task(task.id) });
  }
  const inbox = crew.reviewInbox;
  const pulls = [...(inbox?.assigned ?? []), ...(inbox?.direct ?? []), ...(inbox?.mine ?? []), ...(inbox?.team ?? [])];
  for (const pull of new Map(pulls.map((p) => [`${p.repo}#${p.number}`, p])).values()) {
    items.push({ key: `pull:${pull.repo}#${pull.number}`, title: pull.title, sub: `${pull.repo}#${pull.number} · ${pull.author}`, at: pull.updated_at ?? undefined, icon: "branch", group: "Pull request", run: () => go.pull(pull.repo, pull.number) });
  }
  for (const [key, title, icon] of PAGES) items.push({ key, title, icon, group: "Page", run: () => go.chat(key) });

  const paused = crew.crewState?.paused;
  const chat = crew.chats.find((c) => c.key === current);
  items.push(
    { key: "cmd:new-group", title: "New group chat", icon: "compose", group: "Command", run: go.newGroup },
    { key: "cmd:pause", title: paused ? "Resume the crew" : "Pause the crew", sub: paused ? "Let bots start turns again" : "No new turns until you resume", icon: paused ? "play" : "pause", group: "Command", run: () => crew.pause(!paused) },
    { key: "cmd:usage", title: "Usage", sub: "What's left on your Claude and Codex plans", icon: "chart", group: "Command", run: () => (chat ? go.panel("usage") : go.chat("page:analytics")) },
    { key: "cmd:sidebar", title: "Show or hide the sidebar", icon: "sidebar", group: "Command", run: go.sidebar },
    { key: "cmd:update", title: "Check for updates", icon: "repeat", group: "Command", run: go.updates },
    { key: "cmd:new-bot", title: "New bot", icon: "plus", group: "Command", run: () => go.editBot("") },
  );
  if (chat) {
    const pins = pinsOf(crew.chats);
    const pinned = pins.includes(chat.key);
    items.push({ key: "cmd:pin", title: pinned ? "Unpin this chat" : "Pin this chat to the top", icon: "plus", group: "Command", run: () => crew.setPins(movePin(pins, chat.key, pinned ? null : pins.length)) });
  }
  if (chat?.kind === "direct") {
    items.push({ key: "cmd:status", title: "Status", sub: "This chat's model, session and context, and its plan's limits", icon: "settings", group: "Command", run: () => go.panel("status") });
  }
  for (const bot of crew.bots) {
    items.push({ key: `edit:${bot.name}`, title: `Edit ${labelOf(bot.name)}`, sub: "Its role card", icon: "settings", group: "Command", run: () => go.editBot(bot.name) });
  }
  return items;
}
