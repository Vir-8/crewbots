// The sidebar's conversations: which show for a search, in what order, and which are archived out of the way.

import type { IconName } from "./components/Icon";
import type { Chat } from "./types";

/** The crew's own pages, as the menu at the bottom of the sidebar lists them. */
export const PAGES: [string, string, IconName][] = [
  ["page:crew", "Crew", "users"],
  ["page:projects", "Projects", "folder"],
  ["page:activity", "Activity", "handoff"],
  ["page:tools", "Tools", "tool"],
  ["page:routines", "Routines", "repeat"],
  ["page:analytics", "Analytics", "chart"],
  ["page:settings", "Settings", "settings"],
];

/** The chats a search matches (by title, bot, last message or member): the pinned ones first, in your order, then the
 * rest, most recent first. The rail shows them all. */
export function orderChats(chats: Chat[], query: string, rail = false): Chat[] {
  const q = query.trim().toLowerCase();
  return chats
    .filter((c) => rail || !q || [c.title, c.bot, c.last?.text, ...(c.room?.members ?? [])].some((s) => s?.toLowerCase().includes(q)))
    .sort((a, b) => (a.pinned ?? Infinity) - (b.pinned ?? Infinity) || b.updated_at.localeCompare(a.updated_at));
}

/** The pinned chats' keys, in order. */
export function pinsOf(chats: Chat[]): string[] {
  return chats.filter((c) => c.pinned != null).sort((a, b) => a.pinned! - b.pinned!).map((c) => c.key);
}

/** The pins with `key` put at `to` (pinning it if it wasn't), or taken out when `to` is null. */
export function movePin(pins: string[], key: string, to: number | null): string[] {
  const rest = pins.filter((k) => k !== key);
  if (to === null) return rest;
  const at = Math.max(0, Math.min(to, rest.length));
  return [...rest.slice(0, at), key, ...rest.slice(at)];
}

/** The chats to list, and apart from them the archived group chats, which show only when asked for or searched. */
export function archivedApart(chats: Chat[], query: string): [Chat[], Chat[]] {
  if (query.trim()) return [chats, []];
  return [chats.filter((c) => !c.room?.archived_at), chats.filter((c) => c.room?.archived_at)];
}

/** The most recent conversation, leaving archived group chats out: one with something said in it before an empty one,
 * however new (a bot's chat is made, empty, the first time the app lists it). */
export function latestChat(chats: Chat[]): string | null {
  return [...archivedApart(chats, "")[0]].sort((a, b) => Number(Boolean(b.last)) - Number(Boolean(a.last)) || b.updated_at.localeCompare(a.updated_at))[0]?.key ?? null;
}

/** The conversation to open at start: the one you had open last time, if it's still there; else your first pinned
 * one; else the latest. */
export function chatToOpen(chats: Chat[], last: string | null): string | null {
  const open = archivedApart(chats, "")[0];
  if (last && open.some((c) => c.key === last)) return last;
  return pinsOf(open)[0] ?? latestChat(open);
}
