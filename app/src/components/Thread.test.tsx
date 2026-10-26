import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Crew } from "../state";
import type { StoredEvent, Task } from "../types";
import { Thread } from "./Thread";

// A chat that has been going for weeks: what the app draws when it's opened.
function chat(size: number): { crew: Crew; task: Task } {
  const task = { id: "t_0000000001", bot: "engineer", title: "Chat with engineer", status: "idle", kind: "chat", parent_id: null } as Task;
  const events: StoredEvent[] = [];
  for (let i = 0; i < size; i++) {
    const at = new Date(Date.UTC(2026, 9, 1) + i * 60_000).toISOString();
    const kind = ["message", "tool_call", "tool_result", "text", "usage"][i % 5];
    const data =
      kind === "message" ? { from: "user", text: `Can you look at issue ${i}? The **export** breaks on \`page ${i}\`.` }
      : kind === "tool_call" ? { id: `c${i}`, name: "Bash", input: { command: `pytest -q tests/test_${i}.py` } }
      : kind === "tool_result" ? { id: `c${i - 1}`, output: `${i} passed`, is_error: false }
      : kind === "text" ? { text: `Fixed it:\n\n- the cursor resets on page ${i}\n- a test covers it\n\nSee t_0123456789.` }
      : { input_tokens: 1000, output_tokens: 50 };
    events.push({ id: i + 1, task_id: task.id, kind, data, at });
  }
  const crew = {
    events: new Map([[task.id, events]]),
    ensureEvents: () => undefined,
    children: new Map(),
    tasks: new Map([[task.id, task]]),
    latest: new Map(),
    release: () => Promise.resolve(),
  } as unknown as Crew;
  return { crew, task };
}

describe("opening a long chat", () => {
  it("draws 2,000 events within its budget", () => {
    const { crew, task } = chat(2000);
    const times: number[] = [];
    for (let run = 0; run < 3; run++) {
      const started = performance.now();
      const html = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
      times.push(performance.now() - started);
      expect(html).toContain("issue 1995");
    }
    // Rendering only, in Node, with no layout; the browser adds layout on top (see scripts/perf.py's notes).
    expect(Math.min(...times)).toBeLessThan(150);
    console.log(`2,000-event chat rendered in ${Math.min(...times).toFixed(0)}ms (best of 3)`);
  });
});
