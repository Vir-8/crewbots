import { describe, expect, it } from "vitest";
import { archivedApart, chatToOpen, latestChat, movePin, orderChats, pinsOf } from "./sidebar";
import type { DirectChatOut } from "./api-types";
import type { Chat, Room } from "./types";

const chat = (key: string, updated: string, fields: Partial<DirectChatOut> = {}): Chat => ({
  key, kind: "direct", title: key, bot: key, task_id: key, room: null, last: null, unread: 0, working: false,
  updated_at: updated, ...fields,
});

const group = (id: string, updated: string, room: Partial<Room> = {}): Chat => ({
  key: `room:${id}`, kind: "group", title: room.title ?? id, bot: null, task_id: null, last: null, unread: 0,
  working: false, updated_at: updated,
  room: { id, title: id, lead: "chief", members: ["chief"], created_at: "", updated_at: "", ...room },
});

describe("the sidebar's chats", () => {
  const chats = [
    chat("chief", "2026-10-26T09:00:00Z", { last: { author: "chief", text: "Merged the CSV export", at: "" } }),
    chat("critic", "2026-10-26T11:00:00Z"),
    group("r1", "2026-10-26T10:00:00Z", { title: "Release", members: ["chief", "engineer"] }),
  ];

  it("lists the most recent first", () => {
    expect(orderChats(chats, "").map((c) => c.key)).toEqual(["critic", "room:r1", "chief"]);
  });

  it("matches a search against titles, bots, last messages and members", () => {
    expect(orderChats(chats, " csv ").map((c) => c.key)).toEqual(["chief"]);
    expect(orderChats(chats, "ENGINEER").map((c) => c.key)).toEqual(["room:r1"]);
    expect(orderChats(chats, "nothing")).toEqual([]);
  });

  it("shows every chat on the rail, whatever the search", () => {
    expect(orderChats(chats, "nothing", true)).toHaveLength(3);
  });

  it("leaves the list it was given as it was", () => {
    const before = chats.map((c) => c.key);
    orderChats(chats, "");
    expect(chats.map((c) => c.key)).toEqual(before);
  });
});

describe("archived group chats", () => {
  const room = (id: string, archived_at: string | null) => group(id, "2026-10-26T10:00:00Z", { archived_at });
  const chats = [chat("chief", "2026-10-26T09:00:00Z"), room("live", null), room("old", "2026-10-25T10:00:00Z")];

  it("are kept apart from the list", () => {
    const [listed, archived] = archivedApart(chats, "");
    expect(listed.map((c) => c.key)).toEqual(["chief", "room:live"]);
    expect(archived.map((c) => c.key)).toEqual(["room:old"]);
  });

  it("are in the list when a search finds them", () => {
    expect(archivedApart(chats, "old")[0]).toHaveLength(3);
  });

  it("aren't opened when no conversation is", () => {
    expect(latestChat([chat("chief", "2026-10-26T09:00:00Z"), room("old", "2026-10-26T12:00:00Z")])).toBe("chief");
    expect(latestChat([])).toBeNull();
  });
});

describe("pinned chats", () => {
  const chats = [
    chat("chief", "2026-10-26T09:00:00Z", { pinned: 1 }),
    chat("critic", "2026-10-26T11:00:00Z"),
    chat("engineer", "2026-10-26T08:00:00Z", { pinned: 0 }),
    chat("reviewer", "2026-10-26T12:00:00Z"),
  ];

  it("come first in your order, the rest most recent first, and the latest chat is still the latest", () => {
    expect(orderChats(chats, "").map((c) => c.key)).toEqual(["engineer", "chief", "reviewer", "critic"]);
    expect(pinsOf(chats)).toEqual(["engineer", "chief"]);
    expect(latestChat(chats)).toBe("reviewer");
  });

  it("open at start where you were, else at your first pinned chat, else the latest to have anything in it", () => {
    expect(chatToOpen(chats, "critic")).toBe("critic");
    expect(chatToOpen(chats, "room:gone")).toBe("engineer");
    expect(chatToOpen(chats, null)).toBe("engineer");
    const unpinned = chats.map((c) => ({ ...c, pinned: null }));
    unpinned[0] = { ...unpinned[0], last: { author: "chief", text: "Hey!", at: "" } }; // the chief has said something
    expect(chatToOpen(unpinned, null)).toBe("chief"); // ahead of newer, empty chats
  });

  it("move, pin and unpin", () => {
    expect(movePin(["a", "b", "c"], "c", 0)).toEqual(["c", "a", "b"]);
    expect(movePin(["a", "b", "c"], "a", 9)).toEqual(["b", "c", "a"]);
    expect(movePin(["a", "b"], "z", 1)).toEqual(["a", "z", "b"]);
    expect(movePin(["a", "b"], "a", null)).toEqual(["b"]);
    expect(movePin(["a", "b"], "a", -1)).toEqual(["a", "b"]);
  });
});
