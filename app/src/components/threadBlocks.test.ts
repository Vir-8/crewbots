import { expect, it } from "vitest";
import type { StoredEvent, Task } from "../types";
import { type Block, build, fold, said, settleWakes } from "./threadBlocks";

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

it("tags what the chief told the user from a group chat with where it came from", () => {
  const told = (id: number, level: string): StoredEvent => ({ id, task_id: "chat", kind: "text", data: { text: "Turn on UnityMCP", level, from: "Engineering" }, at: "" });
  expect(build([told(1, "question")], [], new Map())[0]).toMatchObject({ kind: "say", tag: "From Engineering" });
  expect(build([told(2, "needs_you")], [], new Map())[0]).toMatchObject({ kind: "say", tag: "Needs you · From Engineering" });
  const checkin: StoredEvent = { id: 3, task_id: "chat", kind: "text", data: { text: "CI is red", checkin: true, level: "question" }, at: "" };
  expect(build([checkin], [], new Map())[0]).toMatchObject({ tag: "Checked in on its own" });
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

const ev = (id: number, kind: string, data: Record<string, unknown>): StoredEvent => ({ id, task_id: "chat", kind, data, at: "" });
const shownIn = (events: StoredEvent[]) => fold(settleWakes(events, build(events, [], new Map())));
const turn = (id: number, system = false) => ev(id, "turn_start", { system });
const sys = (id: number) => turn(id, true);
const call = (id: number) => ev(id, "tool_call", { id: `c${id}`, name: "Read", input: {} });
const notice = (id: number, message = "State changed: 1 item needs you.", extra = {}) => ev(id, "state_notice", { message, ...extra });
const kinds = (events: StoredEvent[]) => shownIn(events).map((b) => b.kind);

it("shows nothing for a system wake that ends silent", () => {
  const events = [
    turn(0),
    notice(1),
    ev(2, "message", { from: "crewbots", text: "Still open for you, with nothing run for a while" }),
    sys(3),
    call(4),
    ev(5, "tool_call", { id: "b", name: "mcp__crewbots__write_page", input: { page: "Scratchpad" } }),
  ];
  expect(shownIn(events)).toEqual([]);
});

it("shows only the reply to a system wake, with the wake inside its folded steps", () => {
  const blocks = shownIn([turn(0), notice(1), sys(2), call(3), ev(4, "text", { text: "Two things need you." })]);
  expect(blocks.map((b) => b.kind)).toEqual(["activity", "say"]);
  const folded = blocks[0] as Extract<Block, { kind: "activity" }>;
  expect(folded.items.map((i) => (i.kind === "line" ? i.text : i.kind))).toEqual(["State changed: 1 item needs you.", "steps"]);
});

it("folds a wake with no steps into the reply, and keeps what needs the user", () => {
  const events = [
    turn(0),
    ev(1, "message", { from: "crewbots", text: "Crew activity since the last notice" }),
    sys(2),
    ev(3, "rate_limit", { status: "limited", plan: "claude", window: "five_hour" }),
    ev(4, "text", { text: "Done." }),
  ];
  expect(kinds(events)).toEqual(["line", "activity", "say"]);
  expect(kinds(events.slice(0, 4))).toEqual(["line"]);
});

it("leaves a user message and its reply alone", () => {
  const events = [ev(1, "message", { from: "user", text: "hi" }), turn(2), call(3), ev(4, "text", { text: "hello" })];
  expect(kinds(events)).toEqual(["you", "steps", "say"]);
  expect(kinds(events.slice(0, 3))).toEqual(["you", "steps"]);
});

it("hides a silent wake's steps but keeps the reply to an earlier wake", () => {
  const events = [
    turn(0), notice(1), sys(2), call(3), ev(4, "text", { text: "Wake A answered." }),
    notice(5, "Crew activity since the last notice"), sys(6), call(7),
  ];
  const blocks = shownIn(events);
  expect(blocks.map((b) => b.kind)).toEqual(["activity", "say"]);
  const folded = blocks[0] as Extract<Block, { kind: "activity" }>;
  expect(folded.items.map((i) => (i.kind === "line" ? i.text : i.kind))).toEqual(["State changed: 1 item needs you.", "steps"]);
  expect(blocks[1]).toMatchObject({ text: "Wake A answered." });
});

it("does not let a notice queued during a user's turn take over that turn's activity", () => {
  const events = [
    ev(1, "message", { from: "user", text: "hi" }), turn(2), call(3),
    notice(4), // queued while the user's turn runs
    call(5), ev(6, "text", { text: "Here you go." }),
    sys(7), call(8), // the notice's own turn, silent
  ];
  const blocks = shownIn(events);
  expect(blocks.map((b) => b.kind)).toEqual(["you", "activity", "say"]);
  const items = (blocks[1] as Extract<Block, { kind: "activity" }>).items;
  expect(items.every((i) => i.kind === "steps")).toBe(true); // both of the user's steps, and no wake line
  expect(items.reduce((n, i) => n + (i.kind === "steps" ? i.calls.length : 0), 0)).toBe(2);
});

it("shows an old state-full notice that has no warn flag, even after a silent wake", () => {
  const blocks = shownIn([turn(0), notice(1, "State full or unavailable: @critic needs you (x); tidy state."), sys(2), call(3)]);
  expect(blocks).toEqual([expect.objectContaining({ kind: "line", tone: "warn" })]);
});

it("leaves events from before the first turn_start as they were, and no turn takes them", () => {
  const events = [notice(1), call(2), turn(3), call(4)];
  const blocks = shownIn(events);
  // the old notice and steps stay (as one folded run); the silent first turn has nothing to take from them
  expect(blocks.map((b) => b.kind)).toEqual(["activity"]);
  expect(JSON.stringify(blocks)).toContain("State changed");
});

it("a retry's activity and a notice queued during backoff: the next real turn takes the notice", () => {
  // one turn_start for the user's turn (retries add none); the notice's own turn follows
  const events = [ev(1, "message", { from: "user", text: "hi" }), turn(2), call(3), ev(4, "done", { is_error: true, error: "overloaded" }), notice(5), call(6), ev(7, "text", { text: "Done." }), sys(8), call(9)];
  const blocks = shownIn(events);
  expect(blocks.some((b) => b.kind === "say")).toBe(true);
  expect(JSON.stringify(blocks)).not.toContain("State changed"); // the notice's turn 8 was silent
  expect(blocks.filter((b) => b.kind === "steps" || b.kind === "activity").length).toBeGreaterThan(0);
});

it("keeps a user's steps visible when the turn resumes after a restart and ends silent", () => {
  // the notice arrived mid-turn; the daemon restarted; the resumed turn adds no turn_start and says nothing
  const events = [ev(1, "message", { from: "user", text: "build it" }), turn(2), call(3), notice(4), ev(5, "restarted", { again: false }), call(6)];
  const blocks = shownIn(events);
  expect(blocks[0]).toMatchObject({ kind: "you" });
  const steps = blocks.flatMap((b) => (b.kind === "activity" ? b.items : [b])).filter((b) => b.kind === "steps");
  expect(steps.reduce((n, b) => n + (b.kind === "steps" ? b.calls.length : 0), 0)).toBe(2);
});

it("keeps the steps of a resumed turn that carries a user's message, marked as the user's", () => {
  const events = [sys(0), notice(1), ev(2, "message", { from: "user", text: "please build it" }), ev(3, "restarted", { again: false }), turn(4), call(5)];
  expect(JSON.stringify(shownIn(events))).toContain("Read");
});

it("takes the wake before a chat's first turn when that turn is marked system", () => {
  expect(shownIn([notice(1), sys(2), call(3)])).toEqual([]); // silent: nothing shows
  const blocks = shownIn([notice(1), sys(2), call(3), ev(4, "text", { text: "On it." })]);
  expect(blocks.map((b) => b.kind)).toEqual(["activity", "say"]);
  expect(JSON.stringify(blocks)).toContain("State changed");
});

it("does not hide anything for a turn_start without the system flag", () => {
  const events = [ev(0, "turn_start", {}), notice(1), ev(2, "turn_start", {}), call(3)];
  expect(JSON.stringify(shownIn(events))).toContain("Read");
});

it("always shows a warning notice, even after a silent wake", () => {
  const events = [turn(0), notice(1, "State full or unavailable: @critic needs you in room r1 (x); tidy state.", { warn: true }), sys(2), call(3)];
  const blocks = shownIn(events);
  expect(blocks).toEqual([expect.objectContaining({ kind: "line", icon: "alert", tone: "warn", text: expect.stringContaining("State full") })]);
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

it("shows a failed try that a later try got past as a quiet line, and one that didn't as an error", () => {
  const stalled = { is_error: true, error: "nothing came back from claude for 20 min", retry: "transient" };
  const recovered = build([turn(0), ev(1, "done", stalled), ev(2, "notice", { message: "Trying again in 30s (try 2 of 4)." }),
    ev(3, "text", { text: "Done." }), ev(4, "done", { final_text: "Done." })], [], new Map());
  expect(recovered[0]).toMatchObject({ kind: "line", text: "Tried again after: nothing came back from claude for 20 min" });
  expect(recovered[0]).not.toHaveProperty("tone");
  const gaveUp = build([turn(0), ev(1, "done", stalled), ev(2, "done", { ...stalled, retry: null })], [], new Map());
  expect(gaveUp.map((b) => b.kind === "line" && b.tone)).toEqual(["bad", "bad"]);
});

it("shows nothing for a silent system wake whose first try stalled", () => {
  const events = [turn(0), notice(1), sys(2), call(3),
    ev(4, "done", { is_error: true, error: "nothing came back from claude for 20 min", retry: "transient" }),
    ev(5, "notice", { message: "Trying again in 30s (try 2 of 4)." }), call(6)];
  expect(shownIn([...events, ev(7, "done", { final_text: "NO_REPLY" })])).toEqual([]);
});
