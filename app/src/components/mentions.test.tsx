import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";
import type { Crew } from "../state";
import type { Chat, Room } from "../types";
import { bots } from "../preview/demo/make";
import { setCrew } from "./Avatar";
import { DirectChat, GroupChat } from "./ChatView";

const crew = { bots, tasks: new Map(), roomMessages: new Map() } as unknown as Crew;
const room = { id: "room", title: "Review", lead: "chief", members: ["chief", "reviewer"] } as Room;
const draft = (key: string, text: string) => localStorage.setItem(`crewbots.draft.${key}`, JSON.stringify({ text, files: [] }));

describe("mention suggestions", () => {
  afterEach(() => localStorage.clear());

  it("offers crew handles in a direct chat", () => {
    setCrew(bots);
    draft("direct", "@eng");
    const chat = { key: "direct", bot: "chief", task_id: null, unread: 0 } as Chat;
    const html = renderToStaticMarkup(<DirectChat crew={crew} chat={chat} onOpen={() => {}} onPage={() => {}} onCustomize={() => {}} onProfile={() => {}} profileOpen={false} onNewGroup={() => {}} />);
    expect(html).toContain('aria-selected="true" class="mention-option on"');
    expect(html).toContain("@engineer");
    expect(html).not.toContain("@everyone");
  });

  it("keeps group suggestions to members and everyone", () => {
    setCrew(bots);
    draft("group", "@");
    const chat = { key: "group", room, unread: 0 } as Chat;
    const html = renderToStaticMarkup(<GroupChat crew={crew} chat={chat} onOpen={() => {}} panelOpen={false} onPanel={() => {}} />);
    expect(html).toContain("@chief");
    expect(html).toContain("@reviewer");
    expect(html).toContain("@everyone");
    expect(html).not.toContain("@engineer");
  });
});
