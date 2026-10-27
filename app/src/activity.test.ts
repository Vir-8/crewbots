import { describe, expect, it } from "vitest";
import { chains, handedOff, pairs } from "./activity";
import type { Task } from "./types";

let clock = 0;
const task = (id: string, bot: string, fields: Partial<Task> = {}): Task =>
  ({
    id, bot, title: id, status: "idle", created_by: "user", parent_id: null, kind: "thread", review_of: null,
    created_at: `2026-10-26T10:00:${String(clock).padStart(2, "0")}Z`, updated_at: `2026-10-26T10:00:${String(clock++).padStart(2, "0")}Z`,
    ...fields,
  }) as Task;
const crew = (...tasks: Task[]) => new Map(tasks.map((t) => [t.id, t]));

describe("the crew's hand-offs", () => {
  const chat = task("chat", "chief", { kind: "chat" });
  const export_ = task("export", "engineer", { parent_id: "chat", created_by: "chief", status: "running" });
  const own = task("own", "engineer", { parent_id: "export", created_by: "engineer" }); // the engineer's own thread
  const question = task("question", "critic", { parent_id: "own", created_by: "engineer", kind: "ask" });
  const review = task("review", "reviewer", { parent_id: "export", created_by: "engineer", review_of: "export", status: "queued" });
  const earlier = task("earlier", "critic", { parent_id: "chat", created_by: "chief", status: "idle" });
  const fromUser = task("mine", "engineer", { parent_id: "chat", created_by: "user" });
  const routine = task("nightly", "engineer", { parent_id: "chat", created_by: "routine:nightly" });
  const tasks = crew(chat, export_, own, question, review, earlier, fromUser, routine);

  it("are what one bot gave another", () => {
    expect([...tasks.values()].filter(handedOff).map((t) => t.id)).toEqual(["export", "question", "review", "earlier"]);
  });

  it("leave out code review's own reviewers, which aren't the crew's bots", () => {
    const lead = task("pr", "reviewer:claude:sonnet:high", { kind: "review" });
    const second = task("pr2", "reviewer:codex:default:high", { kind: "review", parent_id: "pr", created_by: "reviewer:claude:sonnet:high" });
    expect([lead, second].filter(handedOff)).toEqual([]);
  });

  it("form chains from where a bot first handed work on, through the bots' own threads", () => {
    const [fromChat, ...rest] = chains(tasks);
    expect(rest).toEqual([]); // one chain for what was handed on from the chief's chat
    expect(fromChat.steps.map((s) => [s.task.id, s.verb, s.depth])).toEqual([
      ["export", "delegated", 0],
      ["question", "asked", 1],
      ["review", "review", 1],
      ["earlier", "delegated", 0],
    ]);
    expect(fromChat.from?.id).toBe("chat");
    expect(fromChat.busy).toBe(true);
  });

  it("are busy while any step is going, the latest change first", () => {
    const quiet = task("quiet", "critic", { parent_id: "elsewhere", created_by: "reviewer" });
    const [latest, older] = chains(crew(...tasks.values(), task("elsewhere", "reviewer"), quiet));
    expect([latest.steps[0].task.id, latest.busy, older.steps[0].task.id, older.busy]).toEqual(["quiet", false, "export", true]);
  });

  it("count who hands work to whom", () => {
    expect(pairs(tasks, "2026-10-26T00:00:00Z")).toEqual([
      { from: "chief", to: "critic", count: 1 },
      { from: "chief", to: "engineer", count: 1 },
      { from: "engineer", to: "critic", count: 1 },
      { from: "engineer", to: "reviewer", count: 1 },
    ]);
    expect(pairs(tasks, "2026-10-27T00:00:00Z")).toEqual([]);
  });
});
