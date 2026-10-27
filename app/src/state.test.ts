import { describe, expect, it } from "vitest";
import { derive, doing } from "./state";
import type { StoredEvent, Task } from "./types";

const at = new Date(Date.UTC(2026, 9, 27, 7)).toISOString();
const event = (id: number, kind: string, data: Record<string, unknown>): StoredEvent => ({ id, task_id: "t_chat", kind, data, at });

describe("what a bot is doing", () => {
  it("starts over with each message, rather than showing what it said last", () => {
    const tasks = new Map([["t_chat", { id: "t_chat", bot: "chief", status: "running" } as Task]]);
    const said = event(1, "text", { text: "My name is xyz now." });

    expect(doing(derive(tasks, [said]).latest.get("t_chat"))).toBe("My name is xyz now.");
    expect(doing(derive(tasks, [said, event(2, "message", { from: "user", text: "awesome" })]).latest.get("t_chat"))).toBe("");
  });
});
