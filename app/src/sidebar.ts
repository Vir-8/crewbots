// The sidebar's conversations: which show for a search, in what order, and which are archived out of the way.

import type { Chat } from "./types";

/** The chats a search matches (by title, bot, last message or member), most recent first. The rail shows them all. */
export function orderChats(chats: Chat[], query: string, rail = false): Chat[] {
  const q = query.trim().toLowerCase();
  return chats
    .filter((c) => rail || !q || [c.title, c.bot, c.last?.text, ...(c.room?.members ?? [])].some((s) => s?.toLowerCase().includes(q)))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

/** The chats to list, and apart from them the archived group chats, which show only when asked for or searched. */
export function archivedApart(chats: Chat[], query: string): [Chat[], Chat[]] {
  if (query.trim()) return [chats, []];
  return [chats.filter((c) => !c.room?.archived_at), chats.filter((c) => c.room?.archived_at)];
}

/** The conversation to open when none is: the most recent one, leaving archived group chats out. */
export function latestChat(chats: Chat[]): string | null {
  return orderChats(archivedApart(chats, "")[0], "")[0]?.key ?? null;
}
