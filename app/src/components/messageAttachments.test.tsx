import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Crew } from "../state";
import type { Attachment, Chat, Room, StoredEvent, Task } from "../types";
import { GroupChat } from "./ChatView";
import { Thread } from "./Thread";

const attachments: Attachment[] = [
  { id: "image", name: "picture.png", path: "/picture.png", mime: "image/png", size: 1, kind: "image" },
  { id: "file", name: "notes.zip", path: "/notes.zip", mime: "application/zip", size: 1, kind: "file" },
];
const at = "2026-10-27T12:00:00Z";
const task = { id: "task", bot: "chief", status: "idle" } as Task;
const event = { id: 1, task_id: task.id, kind: "message", at, data: { from: "user", text: "Please see these", attachments } } as StoredEvent;
const room = { id: "room", title: "Crew", lead: "chief", members: ["chief"] } as Room;
const crew = {
  bots: [], tasks: new Map([[task.id, task]]), children: new Map(), latest: new Map(),
  events: new Map([[task.id, [event]]]),
  roomMessages: new Map([[room.id, [{ id: 1, room_id: room.id, author: "user", text: "Please see these", attachments, at, depth: 0 }]]]),
} as unknown as Crew;

function expectFilesFirst(html: string) {
  const files = html.indexOf('class="message-files mine');
  const text = html.indexOf('class="you-text"');
  expect(files).toBeGreaterThanOrEqual(0);
  expect(text).toBeGreaterThan(files);
  expect(html.slice(files, text)).toContain("picture.png");
  expect(html.slice(files, text)).toContain("notes.zip");
}

describe("user message attachments", () => {
  it("renders attachments above text in direct chats and compact threads", () => {
    for (const props of [{ chat: true }, { compact: true }]) {
      expectFilesFirst(renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => {}} {...props} />));
    }
  });

  it("renders attachments above text in group chats", () => {
    const chat = { key: "group", room, unread: 0 } as Chat;
    expectFilesFirst(renderToStaticMarkup(<GroupChat crew={crew} chat={chat} onOpen={() => {}} panelOpen={false} onPanel={() => {}} />));
  });
});
