import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { ChatRow } from "./Sidebar";
import type { Chat } from "../types";

it("shows the mute icon while keeping the unread dot for direct and group chats", () => {
  const direct: Chat = { key: "bot:chief", kind: "direct", title: "Chief", bot: "chief", task_id: "chat",
    room: null, last: null, unread: 1, muted: true, working: false, updated_at: "" };
  const group: Chat = { ...direct, key: "room:r", kind: "group", bot: null,
    room: { id: "r", title: "Team", lead: "chief", members: ["chief"], created_at: "", updated_at: "", muted: true } };
  for (const chat of [direct, group]) {
    const html = renderToStaticMarkup(<ChatRow chat={chat} on={false} onClick={() => {}} />);
    expect(html).toContain("chat-row-muted");
    expect(html).toContain("unread-dot");
    expect(renderToStaticMarkup(<ChatRow chat={{ ...chat, muted: false }} on={false} onClick={() => {}} />)).not.toContain("chat-row-muted");
  }
});
