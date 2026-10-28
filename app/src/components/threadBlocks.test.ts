import { expect, it } from "vitest";
import type { StoredEvent } from "../types";
import { build } from "./threadBlocks";

it("distinguishes automatic merges in the thread", () => {
  const event: StoredEvent = { id: 1, task_id: "task", kind: "merged", data: { commit: "abcdef123", automatic: true }, at: "" };
  expect(build([event], [], new Map())).toEqual([
    { kind: "line", id: 1, text: "Merged automatically as abcdef1", icon: "check", tone: "good" },
  ]);
});
