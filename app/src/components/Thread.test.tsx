import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { bridge } from "../bridge";
import type { Crew } from "../state";
import type { StoredEvent, Task } from "../types";
import { Thread } from "./Thread";
import { build, said } from "./threadBlocks";
import { count } from "./Find";

afterEach(() => vi.restoreAllMocks());

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
    bots: [],
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
  it("keeps a work card in history and shows review rounds rather than versions", () => {
    const { crew, task } = chat(0);
    const work = { id: "t_1111111111", bot: "engineer", title: "Old work", status: "idle", kind: "thread", parent_id: task.id, created_by: task.bot, created_at: "2026-10-27T07:01:00Z", merge_state: "merged" } as Task;
    const reviewer = (id: string, version: number) => ({ id, bot: "reviewer", status: "idle", kind: "thread", parent_id: task.id, review_of: work.id, review_version: version } as Task);
    const review = reviewer("t_2222222222", 4);
    crew.children.set(task.id, [work, review]);
    crew.tasks.set(work.id, work);
    crew.family = () => [];
    crew.asks = new Map();
    crew.verdicts = new Map([[work.id, [{ id: 10, task_id: review.id, kind: "review", data: { task_id: review.id, verdict: "pass" }, at: "2026-10-27T07:02:00Z" }]]]);
    crew.events.set(task.id, [
      { id: 1, task_id: task.id, kind: "tool_call", data: { id: "delegate", name: "mcp__crewbots__delegate", input: {} }, at: "2026-10-27T07:00:00Z" },
      { id: 2, task_id: task.id, kind: "tool_result", data: { id: "delegate", output: "Delegated t_1111111111 to engineer: Old work" }, at: "2026-10-27T07:01:00Z" },
      { id: 3, task_id: task.id, kind: "message", data: { from: "user", text: "Newest message" }, at: "2026-10-27T07:03:00Z" },
    ]);
    const render = () => renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    const first = render();
    expect(first.indexOf("Old work")).toBeLessThan(first.indexOf("Newest message"));
    expect(first).toContain("Merged");
    expect(first).not.toContain("round 4 of 3");
    expect(first).toContain("round 1"); // The cap is fetched after the first render.
    crew.children.set(task.id, [work, review, reviewer("t_3333333333", 7), reviewer("t_4444444444", 7)]);
    const second = render();
    expect(second).toContain("round 2");
    expect(second).not.toContain("round 7 of 3");
    expect(second.indexOf("Old work")).toBeLessThan(second.indexOf("Newest message"));
  });

  it("keeps tool images folded while user attachments and shared images stay visible", () => {
    vi.spyOn(bridge, "fileSrc").mockImplementation((path) => path);
    const { crew, task } = chat(0);
    const at = "2026-10-27T07:00:00Z";
    const picture = (name: string) => ({ id: name, name, path: `/attachments/${name}`, mime: "image/png", size: 100, kind: "image" });
    crew.events.set(task.id, [
      { id: 1, task_id: task.id, kind: "message", data: { from: "user", text: "Check this", attachments: [picture("user.png")] }, at },
      { id: 2, task_id: task.id, kind: "text", data: { text: "I'll read it." }, at },
      { id: 3, task_id: task.id, kind: "tool_call", data: { id: "read", name: "Read", input: { file_path: "/attachments/user.png" } }, at },
      { id: 4, task_id: task.id, kind: "tool_result", data: { id: "read", output: "", images: [picture("tool.png")] }, at },
      { id: 5, task_id: task.id, kind: "tool_call", data: { id: "share", name: "mcp__crewbots__share", input: { files: ["shared.png"] } }, at },
      { id: 6, task_id: task.id, kind: "shared", data: { text: "Here's the result.", attachments: [picture("shared.png")] }, at },
      { id: 7, task_id: task.id, kind: "tool_result", data: { id: "share", output: "Shared" }, at },
    ]);
    const html = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    expect(html).toContain("Read a file");
    expect(html).not.toContain('alt="tool.png"');
    expect(html).toContain('alt="user.png"');
    expect(html).toContain('alt="shared.png"');
  });

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

  it("renders and finds Markdown in questions and permission text", () => {
    const { crew, task } = chat(0);
    const at = new Date(Date.UTC(2026, 9, 27, 7)).toISOString();
    const question = "Check `export` first.\n\nThen run:\n\n```sh\nexport report\n```";
    crew.events.set(task.id, [
      { id: 1, task_id: task.id, kind: "escalation", data: { question }, at },
      { id: 2, task_id: task.id, kind: "permission", data: { permission: 7, kind: "network", bot: "engineer", task: task.id, what: "Allow `export`?", why: "Run this:\n\n```sh\nexport report\n```" }, at },
    ]);
    const html = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} find={{ query: "export", current: 0, onFound: () => undefined }} />);
    expect(html).toContain('<p><span class="pre">Check <code><mark class="find-hit">export</mark></code> first.</span></p><p><span class="pre">Then run:</span></p>');
    expect(html.match(/<pre><mark class="find-hit">export<\/mark> report<\/pre>/g)).toHaveLength(2);
    expect(html).toContain('<p><span class="pre">Allow <code><mark class="find-hit">export</mark></code>?</span></p>');
    expect(html).not.toContain("`");
    expect(html.match(/class="find-hit"/g)).toHaveLength(4);
    const blocks = build(crew.events.get(task.id)!, [], crew.tasks);
    expect(blocks.map((block) => count(said(block), "export"))).toEqual([2, 2]);
    expect(blocks.map((block) => count(said(block), "```"))).toEqual([0, 0]);
  });

  it("finds choice labels and descriptions in open and answered questions", () => {
    const { crew, task } = chat(0);
    const at = new Date(Date.UTC(2026, 9, 27, 7)).toISOString();
    const asked: StoredEvent = { id: 1, task_id: task.id, kind: "escalation", data: { question: "Choose:", choices: [{ label: "Export", description: "Export the report" }] }, at };
    for (const answered of [false, true]) {
      crew.events.set(task.id, answered ? [asked, { id: 2, task_id: task.id, kind: "message", data: { from: "user", text: "Export" }, at }] : [asked]);
      const html = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} find={{ query: "export", current: 0, onFound: () => undefined }} />);
      expect(html).toContain('<span class="ask-label"><mark class="find-hit">Export</mark></span>');
      expect(html).toContain('<span class="ask-description"><mark class="find-hit">Export</mark> the report</span>');
      expect(count(said(build(crew.events.get(task.id)!, [], crew.tasks)[0]), "export")).toBe(2);
    }
  });

  it("asks your permission with Allow and Don't allow, and says how it was answered", () => {
    const { crew, task } = chat(0);
    const at = new Date(Date.UTC(2026, 9, 27, 7)).toISOString();
    const asked: StoredEvent = { id: 1, task_id: task.id, kind: "permission", data: { permission: 7, kind: "network", bot: "engineer", task: "t_thread", what: "Install the test dependencies", why: "pytest needs them" }, at };
    crew.tasks.set("t_thread", { ...task, id: "t_thread", kind: "thread", title: "Fix the build", parent_id: task.id });
    crew.events.set(task.id, [asked]);
    const open = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    expect(open).toContain("asks your permission");
    expect(open).toContain("In its thread: Fix the build"); // shown in the chat its thread came from
    expect(open).toContain("Don&#x27;t allow");
    expect(open).toContain("For the rest of this thread");

    crew.events.set(task.id, [asked, { id: 2, task_id: task.id, kind: "permission_answer", data: { permission: 7, allowed: false }, at }]);
    const answered = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    expect(answered).toContain("Not allowed");
    expect(answered).not.toContain("Don&#x27;t allow");
  });

  it("marks where each message sits in a run from one side, which says where its time goes", () => {
    const { crew, task } = chat(0);
    const at = new Date(Date.UTC(2026, 9, 27, 7)).toISOString();
    const later = new Date(Date.UTC(2026, 9, 27, 9)).toISOString(); // two hours on: a time break, and a new run
    crew.events.set(task.id, [
      { id: 1, task_id: task.id, kind: "text", data: { text: "Hey!" }, at },
      { id: 2, task_id: task.id, kind: "text", data: { text: "Message me anytime." }, at },
      { id: 3, task_id: task.id, kind: "text", data: { text: "Want to give me a name?" }, at },
      { id: 4, task_id: task.id, kind: "message", data: { from: "user", text: "Roger" }, at },
      { id: 5, task_id: task.id, kind: "message", data: { from: "user", text: "Morning" }, at: later },
    ]);
    const html = renderToStaticMarkup(<Thread crew={crew} task={task} onOpen={() => undefined} chat />);
    const classes = [...html.matchAll(/class="((?:said|you)(?: [a-z-]+)*)"/g)].map((m) => m[1]);
    expect(classes).toEqual(["said after-break continued", "said joined continued", "said joined", "you", "you after-break"]);
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
