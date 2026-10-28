import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Crew } from "../state";
import type { Chat, Room, Task } from "../types";
import { GroupChat } from "./ChatView";

const room = { id: "room", title: "Engineering", lead: "reviewer", members: ["reviewer", "critic"] } as Room;
const chat = { key: "group", room, unread: 0 } as Chat;

function render(status: Task["status"]) {
  const seat = { id: "seat", kind: "seat", room_id: room.id, bot: "reviewer", status } as Task;
  const crew = {
    bots: [], tasks: new Map([[seat.id, seat]]),
    roomMessages: new Map([[room.id, [{ id: 1, room_id: room.id, author: "user", text: "Hello", at: "2026-10-28T00:00:00Z", depth: 0 }]]]),
  } as unknown as Crew;
  return renderToStaticMarkup(<GroupChat crew={crew} chat={chat} onOpen={() => {}} panelOpen={false} onPanel={() => {}} />);
}

describe("room activity", () => {
  it.each(["queued", "running"] as const)("describes a %s seat as working, without promising a reply", (status) => {
    const html = render(status);
    expect(html).toContain("reviewer is working");
    expect(html).not.toContain("replying");
  });

  it("clears the activity indicator when a seat is idle", () => {
    expect(render("idle")).not.toContain('class="working-indicator"');
  });
});
