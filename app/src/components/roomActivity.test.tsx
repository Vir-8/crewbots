import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Crew } from "../state";
import type { GroupChatOut } from "../api-types";
import type { Room, Task } from "../types";
import { GroupChat } from "./ChatView";

const room = { id: "room", title: "Engineering", lead: "reviewer", members: ["reviewer", "critic"] } as Room;
const chat = { key: "group", kind: "group", room, unread: 0 } as GroupChatOut;

function render(status: Task["status"], attempt: Task["attempt"] = null) {
  const seat = { id: "seat", kind: "seat", room_id: room.id, bot: "reviewer", status, attempt } as Task;
  const crew = {
    bots: [], tasks: new Map([[seat.id, seat]]),
    roomMessages: new Map([[room.id, [{ id: 1, room_id: room.id, author: "user", text: "Hello", at: "2026-10-28T00:00:00Z", depth: 0 }]]]),
  } as unknown as Crew;
  return renderToStaticMarkup(<GroupChat crew={crew} chat={chat} onOpen={() => {}} panelOpen={false} onPanel={() => {}} />);
}

describe("room activity", () => {
  it("describes a running seat as working, without promising a reply", () => {
    const html = render("running");
    expect(html).toContain("reviewer is working");
    expect(html).not.toContain("replying");
  });

  it("does not call a queued seat working", () => {
    expect(render("queued")).not.toContain("working");
  });

  it("shows a seat waiting between tries as retrying, not working", () => {
    const html = render("queued", { attempt: 2, of: 4, wait_seconds: 30 });
    expect(html).toContain("reviewer couldn&#x27;t start, retrying (attempt 2 of 4)");
    expect(html).not.toContain("is working");
  });

  it("clears the activity indicator when a seat is idle", () => {
    expect(render("idle")).not.toContain('class="working-indicator"');
  });
});
