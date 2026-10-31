import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import type { Crew } from "../state";
import type { GroupChatOut } from "../api-types";
import type { Room, RoomMessage } from "../types";
import { GroupChat } from "./ChatView";

const room = { id: "room", title: "Engineering", lead: "engineer", members: ["engineer", "critic"] } as Room;
const chat = { key: "group", kind: "group", room, unread: 0 } as GroupChatOut;
const at = "2026-10-31T12:00:00Z";
const shot = { id: "a", name: "hero.png", path: "/Users/pat/studio/renders/hero.png", mime: "image/png", size: 1, kind: "image" as const };

function links(messages: Partial<RoomMessage>[]) {
  const said = messages.map((m, i) => ({ id: i + 1, room_id: room.id, author: "engineer", depth: 1, at, text: "", ...m }));
  const crew = { bots: [], tasks: new Map(), roomMessages: new Map([[room.id, said]]) } as unknown as Crew;
  const html = renderToStaticMarkup(<GroupChat crew={crew} chat={chat} onOpen={() => {}} panelOpen={false} onPanel={() => {}} />);
  return [...html.matchAll(/class="text-link" title="([^"]*)">([^<]*)</g)].map((m) => [m[2], m[1]]);
}

it("finds a group chat message's relative links in the folder of the task that said it", () => {
  expect(links([{ text: "See [plan](docs/plan.md:12) and [up](../secret.md).", folder: "/Users/pat/.crewbots/worktrees/acme-web/t_1" }]))
    .toEqual([["plan", "/Users/pat/.crewbots/worktrees/acme-web/t_1/docs/plan.md"]]);
});

it("finds them in the files shown under it first, and in nothing else when it has no folder", () => {
  const text = "The [shot](renders/hero.png) and [plan](docs/plan.md).";
  expect(links([{ text, attachments: [shot], folder: "/Users/pat/work" }]))
    .toEqual([["shot", shot.path], ["plan", "/Users/pat/work/docs/plan.md"]]);
  expect(links([{ text, attachments: [shot], folder: null }, { text: "Old [plan](docs/plan.md)." }]))
    .toEqual([["shot", shot.path]]); // from before messages kept their folder
});
