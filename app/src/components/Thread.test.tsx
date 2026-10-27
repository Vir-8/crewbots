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

  it("draws only its latest messages, with a way to earlier ones", () => {
    const { crew, task } = chat(2000);
    const html = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    expect(html).toContain("issue 1995");
    expect(html).not.toContain("issue 5?");
    expect(html).toContain("Earlier messages");
    expect(html.match(/class="you"/g)!.length).toBeLessThan(40); // of 400
    const short = chat(20);
    expect(renderToStaticMarkup(<Thread crew={short.crew} task={short.task} onOpen={() => undefined} chat />)).not.toContain("Earlier messages");
  });
});

describe("a bot's working, in a chat", () => {
  it("folds into one line between two messages, and stays whole in a thread", () => {
    const { crew, task } = chat(0);
    const at = new Date(Date.UTC(2026, 9, 27, 7)).toISOString();
    const events: StoredEvent[] = [
      { id: 1, task_id: task.id, kind: "message", data: { from: "user", text: "Bring it in" }, at },
      { id: 2, task_id: task.id, kind: "tool_call", data: { id: "a", name: "mcp__crewbots__remember", input: { title: "Who the user is" } }, at },
      { id: 3, task_id: task.id, kind: "memory", data: { action: "remember", scope: "crew", title: "Who the user is" }, at },
      { id: 4, task_id: task.id, kind: "memory", data: { action: "remember", scope: "crew", title: "Commits: when and how" }, at },
      { id: 5, task_id: task.id, kind: "tool_call", data: { id: "b", name: "Read", input: { file_path: "/x/MEMORY.md" } }, at },
      { id: 6, task_id: task.id, kind: "tool_call", data: { id: "c", name: "mcp__crewbots__write_page", input: { page: "Scratchpad", text: "..." } }, at },
      { id: 7, task_id: task.id, kind: "text", data: { text: "I've kept what's worth keeping." }, at },
    ];
    crew.events.set(task.id, events);

    const inChat = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    const inThread = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} />);

    expect(inChat).toContain("Kept 2 notes · wrote Scratchpad · 1 step");
    expect(inChat).not.toContain("Remembered for the crew");  // folded away until opened
    expect(inChat).toContain("I&#x27;ve kept what&#x27;s worth keeping.");
    expect(inThread).toContain("Remembered for the crew: Who the user is") ;
    expect(inThread).toContain("Wrote");
  });

  it("says what a run of housekeeping was", () => {
    const { crew, task } = chat(0);
    const at = new Date(Date.UTC(2026, 9, 27, 7)).toISOString();
    crew.events.set(task.id, [
      { id: 1, task_id: task.id, kind: "flushed", data: { logged: 3 }, at },
      { id: 2, task_id: task.id, kind: "compacted", data: { trigger: "auto", before: 131000, after: 8000 }, at },
      { id: 3, task_id: task.id, kind: "worktree", data: { branch: "crew/chief/notes" }, at },
    ]);
    expect(renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />)).toContain("Compacted its conversation · 1 update");
  });

  it("draws a question's choices with Other, and what was picked once it's answered", () => {
    const { crew, task } = chat(0);
    const at = new Date(Date.UTC(2026, 9, 27, 7)).toISOString();
    const asked: StoredEvent = { id: 1, task_id: task.id, kind: "escalation", data: { question: "Which way?", choices: [{ label: "Recharts", description: "Port 6 charts" }, { label: "Stay on 18" }] }, at };
    crew.events.set(task.id, [asked]);
    const open = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    expect(open).toContain("Needs your answer");
    expect(open).toContain("Port 6 charts");
    expect(open).toContain("Answer in your own words");

    crew.events.set(task.id, [asked, { id: 2, task_id: task.id, kind: "message", data: { from: "user", text: "Stay on 18" }, at }]);
    const answered = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    expect(answered).toContain("Answered");
    expect(answered).toMatch(/ask-choice picked"[^>]*>(?:(?!<\/button>).)*Stay on 18/);
    expect(answered).not.toContain("Answer in your own words");
  });

  it("says so when a bot takes the name it's given", () => {
    const { crew, task } = chat(0);
    const at = new Date(Date.UTC(2026, 9, 27, 7)).toISOString();
    crew.events.set(task.id, [
      { id: 1, task_id: task.id, kind: "tool_call", data: { id: "a", name: "mcp__crewbots__set_name", input: { name: "Roger" } }, at },
      { id: 2, task_id: task.id, kind: "tool_result", data: { id: "a", output: "You go by Roger now" }, at },
    ]);
    const html = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    expect(html).toContain("Took the name Roger");
    expect(html).not.toContain("set_name");
  });
});
