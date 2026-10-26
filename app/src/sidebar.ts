// The sidebar's conversations: which show for a search, and in what order.

import type { Chat } from "./types";

/** The chats a search matches (by title, bot, last message or member), most recent first. The rail shows them all. */
export function orderChats(chats: Chat[], query: string, rail = false): Chat[] {
  const q = query.trim().toLowerCase();
  return chats
    .filter((c) => rail || !q || [c.title, c.bot, c.last?.text, ...(c.room?.members ?? [])].some((s) => s?.toLowerCase().includes(q)))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}
