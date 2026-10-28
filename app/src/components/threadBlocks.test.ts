import { expect, it } from "vitest";
import type { StoredEvent, Task } from "../types";
import { build, said } from "./threadBlocks";

it("distinguishes automatic merges in the thread", () => {
  const event: StoredEvent = { id: 1, task_id: "task", kind: "merged", data: { commit: "abcdef123", automatic: true }, at: "" };
  expect(build([event], [], new Map())).toEqual([
    { kind: "line", id: 1, text: "Merged automatically as abcdef1", icon: "check", tone: "good" },
  ]);
});

it("shows files left uncommitted by a merge in the thread", () => {
  const event: StoredEvent = { id: 1, task_id: "task", kind: "merged", data: { commit: "abcdef123", leftover_files: ["Assets/Vir.png", "new file"] }, at: "" };
  expect(build([event], [], new Map())[0]).toMatchObject({ text: "Merged as abcdef1 · Saved after the merge started, still uncommitted: Assets/Vir.png, new file" });
});

it("shows withdrawn proposals and searches the displayed folder", () => {
  const event: StoredEvent = { id: 1, task_id: "task", kind: "merge_withdrawn", data: { by: "user", reason: "Superseded" }, at: "" };
  expect(build([event], [], new Map())[0]).toMatchObject({ text: "You withdrew the merge proposal: Superseded" });
  expect(said({ kind: "permission", id: 2, permission: 1, bot: "chief", taskId: "task", ask: "folder", what: "Write", why: "", folder: "/Users/apple/Documents/test", here: true })).toBe("Write\n~/Documents/test");
});

it("anchors each work card to its own delegation or creation, even as its status changes", () => {
  const at = (minute: number) => `2026-10-27T10:${String(minute).padStart(2, "0")}:00Z`;
  const child = (id: string, minute: number) => ({ id, created_at: at(minute), status: "idle", kind: "thread", review_of: null } as Task);
  const older = child("t_1111111111", 2);
  const manual = child("t_2222222222", 4);
  const newer = child("t_3333333333", 6);
  const event = (id: number, minute: number, kind: string, data: Record<string, unknown>): StoredEvent => ({ id, task_id: "chat", kind, data, at: at(minute) });
  const events = [
    event(1, 1, "tool_call", { id: "old", name: "mcp__crewbots__delegate", input: {} }),
    event(2, 2, "tool_result", { id: "old", output: "Delegated t_1111111111 to engineer: old" }),
    event(3, 3, "message", { from: "user", text: "After old work" }),
    event(4, 5, "tool_call", { id: "new", name: "mcp__crewbots__start_thread", input: {} }),
    event(5, 6, "tool_result", { id: "new", output: "Opened thread t_3333333333: new" }),
    event(6, 7, "message", { from: "user", text: "Latest message" }),
  ];
  const positions = (children: Task[]) => build(events, children, new Map()).map((b) => b.kind === "work" ? b.task.id : b.id);
  expect(positions([older, manual, newer])).toEqual([older.id, 3, manual.id, newer.id, 6]);
  expect(positions([{ ...older, merge_state: "merged", updated_at: at(9) }, manual, newer])).toEqual([older.id, 3, manual.id, newer.id, 6]);
  expect(positions([newer, manual, older])).toEqual([older.id, 3, manual.id, newer.id, 6]);
});

it("places a child with a missing tool result at its creation time, without claiming another call", () => {
  const child = { id: "t_1111111111", created_at: "2026-10-27T10:02:00Z", kind: "thread" } as Task;
  const events: StoredEvent[] = [
    { id: 1, task_id: "chat", kind: "tool_call", data: { id: "failed", name: "mcp__crewbots__delegate", input: {} }, at: "2026-10-27T10:00:00Z" },
    { id: 2, task_id: "chat", kind: "tool_result", data: { id: "failed", output: "No budget", is_error: true }, at: "2026-10-27T10:01:00Z" },
    { id: 3, task_id: "chat", kind: "message", data: { from: "user", text: "Later" }, at: "2026-10-27T10:03:00Z" },
  ];
  expect(build(events, [child], new Map()).map((b) => b.kind === "work" ? b.task.id : b.id)).toEqual([1, child.id, 3]);
});

it("pairs asks with their own task even when children or results are out of order or missing", () => {
  const child = (id: string, result: string) => ({ id, kind: "ask", result } as Task);
  const first = child("t_1111111111", "First answer");
  const second = child("t_2222222222", "Second answer");
  const event = (id: number, kind: string, data: Record<string, unknown>): StoredEvent => ({ id, task_id: "chat", kind, data, at: "" });
  const events = [
    event(1, "tool_call", { id: "first", name: "mcp__crewbots__ask", input: { bot: "critic", question: "First?" } }),
    event(2, "tool_call", { id: "missing", name: "mcp__crewbots__ask", input: { bot: "reviewer", question: "Missing?" } }),
    event(3, "tool_call", { id: "second", name: "mcp__crewbots__ask", input: { bot: "reviewer", question: "Second?" } }),
    event(4, "tool_result", { id: "second", output: "Asked reviewer (t_2222222222). End your turn." }),
    event(5, "tool_result", { id: "first", output: "Asked critic (t_1111111111). End your turn." }),
  ];
  const asks = build(events, [second, first], new Map()).filter((b) => b.kind === "asked");
  expect(asks.map((b) => b.kind === "asked" && b.task?.result)).toEqual(["First answer", undefined, "Second answer"]);
  expect(build(events, [second], new Map()).filter((b) => b.kind === "asked").map((b) => b.kind === "asked" && b.task?.result)).toEqual([undefined, undefined, "Second answer"]);
});

it("folds a shared-state notice on its own", () => {
  const notice: StoredEvent = { id: 1, task_id: "chat", kind: "state_notice", data: { message: "State changed: 1 item needs you." }, at: "" };
  const blocks = build([notice], [], new Map());
  expect(blocks).toEqual([{ kind: "activity", id: 1, items: [{ kind: "line", id: 1, text: "State changed: 1 item needs you.", gist: "State changed: 1 item needs you." }] }]);
});

it("keeps a question open after a teammate message until the user answers", () => {
  const events: StoredEvent[] = [
    { id: 1, task_id: "chat", kind: "escalation", data: { question: "Which?" }, at: "" },
    { id: 2, task_id: "chat", kind: "message", data: { from: "helper", text: "Suggestion" }, at: "" },
  ];
  expect(build(events, [], new Map())[0]).toMatchObject({ kind: "ask", answer: undefined });
  events.push({ id: 3, task_id: "chat", kind: "message", data: { from: "user", text: "Yes" }, at: "" });
  expect(build(events, [], new Map())[0]).toMatchObject({ kind: "ask", answer: "Yes" });
});

it("routes a room question to its seat and pairs only its own answer", () => {
  const events: StoredEvent[] = [
    { id: 1, task_id: "chat", kind: "seat_escalation", data: { task: "seat", question_id: 42, room: "Engineering", question: "Which?" }, at: "" },
    { id: 2, task_id: "chat", kind: "message", data: { from: "user", text: "Unrelated" }, at: "" },
    { id: 3, task_id: "chat", kind: "seat_escalation_answer", data: { question_id: 43, text: "Other answer" }, at: "" },
  ];
  expect(build(events, [], new Map())[0]).toMatchObject({ kind: "ask", taskId: "seat", room: "Engineering", answer: undefined });
  events.push({ id: 4, task_id: "chat", kind: "seat_escalation_answer", data: { question_id: 42, text: "Yes" }, at: "" });
  expect(build(events, [], new Map())[0]).toMatchObject({ answer: "Yes" });
  events.push({ id: 5, task_id: "chat", kind: "seat_escalation_closed", data: { question_id: 42 }, at: "" });
  expect(build(events, [], new Map())[0]).toMatchObject({ answer: "Yes", closed: true });
});

it("closes the superseded and deleted-room cards without closing other questions", () => {
  const events: StoredEvent[] = [
    { id: 1, task_id: "chat", kind: "seat_escalation", data: { task: "seat", question_id: 10, room: "Engineering", question: "Use SQLite?" }, at: "" },
    { id: 2, task_id: "chat", kind: "seat_escalation_closed", data: { question_id: 10 }, at: "" },
    { id: 3, task_id: "chat", kind: "seat_escalation", data: { task: "seat", question_id: 11, room: "Engineering", question: "Release Friday?" }, at: "" },
  ];
  let asks = build(events, [], new Map()).filter((b) => b.kind === "ask");
  expect(asks[0]).toMatchObject({ questionId: 10, closed: true, answer: undefined });
  expect(asks[1]).toMatchObject({ questionId: 11, closed: false, answer: undefined });
  events.push({ id: 4, task_id: "chat", kind: "seat_escalation_closed", data: { question_id: 11 }, at: "" });
  asks = build(events, [], new Map()).filter((b) => b.kind === "ask");
  expect(asks[1]).toMatchObject({ closed: true, answer: undefined });
});
