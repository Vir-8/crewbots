import { describe, expect, it } from "vitest";
import { type Go, paletteItems } from "./components/paletteItems";
import { rank, score } from "./palette";
import { PAGES } from "./sidebar";
import type { Crew } from "./state";
import type { Task } from "./types";

const entries = [
  { key: "bot:engineer", title: "Engineer", sub: "Writes and changes code", at: "2026-10-27T09:00:00Z" },
  { key: "bot:reviewer", title: "Reviewer", sub: "Checks work", at: "2026-10-27T10:00:00Z" },
  { key: "task:t_1", title: "Add CSV export to the reports page", at: "2026-10-26T10:00:00Z" },
  { key: "page:settings", title: "Settings" },
  { key: "cmd:new-group", title: "New group chat" },
];

describe("the command palette's matching", () => {
  it("matches letters in order, best at word starts and together", () => {
    expect(score("Engineer", "eng")).toBeGreaterThan(score("Reviewer", "e"));
    expect(score("New group chat", "ngc")).toBeGreaterThan(0);
    expect(score("Settings", "xyz")).toBe(-1);
    expect(score("Add CSV export", "csv")).toBeGreaterThan(score("Add a cassava", "csv"));
  });

  it("puts the best match first, and finds things by what they are as well as their name", () => {
    expect(rank(entries, "csv", []).map((e) => e.key)).toEqual(["task:t_1"]);
    expect(rank(entries, "set", [])[0].key).toBe("page:settings");
    expect(rank(entries, "code", [])[0].key).toBe("bot:engineer"); // "Writes and changes code"
  });

  it("with nothing typed, shows what you picked lately, then the most recent", () => {
    expect(rank(entries, "", ["page:settings"]).map((e) => e.key).slice(0, 3)).toEqual(["page:settings", "bot:reviewer", "bot:engineer"]);
    expect(rank(entries, "er", ["bot:engineer"])[0].key).toBe("bot:engineer"); // picked lately: first among matches
  });
});

describe("what the command palette offers", () => {
  it("every page, and a thread's status as the rest of the app says it", () => {
    const task = { id: "t_1", kind: "thread", review_of: null, title: "Add CSV export", bot: "engineer", status: "idle", updated_at: "" } as Task;
    const crew = { chats: [], tasks: new Map([[task.id, task]]), bots: [], reviewInbox: null, crewState: null } as unknown as Crew;
    const items = paletteItems(crew, null, {} as Go);
    expect(items.filter((i) => i.group === "Page").map((i) => i.key)).toEqual(["review", "page:landed", ...PAGES.map(([key]) => key)]);
    expect(items.find((i) => i.key === "task:t_1")?.sub).toBe("engineer · done");
  });
});
