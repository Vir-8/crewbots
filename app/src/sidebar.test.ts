import { describe, expect, it } from "vitest";
import { archivedApart, latestChat, orderChats } from "./sidebar";
import type { Chat } from "./types";

const chat = (key: string, updated: string, fields: Partial<Chat> = {}): Chat => ({
  key, kind: "direct", title: key, bot: key, task_id: null, room: null, last: null, unread: 0, working: false,
  updated_at: updated, ...fields,
});

describe("the sidebar's chats", () => {
  const chats = [
    chat("chief", "2026-10-26T09:00:00Z", { last: { author: "chief", text: "Merged the CSV export", at: "" } }),
    chat("critic", "2026-10-26T11:00:00Z"),
    chat("room:r1", "2026-10-26T10:00:00Z", {
      kind: "group", title: "Release", bot: null,
      room: { id: "r1", title: "Release", lead: "chief", members: ["chief", "engineer"], created_at: "", updated_at: "" },
    }),
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
  const room = (id: string, archived_at: string | null) =>
    chat(`room:${id}`, "2026-10-26T10:00:00Z", { kind: "group", title: id, bot: null, room: { id, title: id, lead: "chief", members: ["chief"], created_at: "", updated_at: "", archived_at } });
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
