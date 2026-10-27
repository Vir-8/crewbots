// Chats: one ongoing chat with each bot. The chief's opened the work above as threads, so they hang from it.

import { files } from "./files";
import { bots, done, emit, message, say, task, tasks, tick, tool } from "./make";
import type { Attachment, Room } from "../../types";

export const chatIds: Record<string, string> = {};
for (const b of bots) {
  chatIds[b.name] = task(`t_chat_${b.name}`.slice(0, 12).padEnd(12, "0"), b.name, `Chat with ${b.name}`, { kind: "chat", workspace: null }).id;
}
{
  const chief = chatIds.chief;
  const opened = ["t_truncate01", "t_docs000001", "t_login00001", "t_react00001", "t_csv0000001"].filter((id) => tasks.has(id));
  for (const id of opened) tasks.set(id, { ...tasks.get(id)!, parent_id: chief });
  message(chief, "user", "Add truncate(s, n) to text.py, with tests, and have it reviewed before it lands.");
  tool(chief, "mcp__crewbots__start_thread", { title: "Add truncate(s, n)", goal: "…" }, "Opened thread");
  say(chief, "On it. I've opened a thread for it; the engineer writes it and two reviewers check it.");
  message(chief, "user", "Also: translate the onboarding docs into Spanish, fix the flaky login test, and look at upgrading to React 19.");
  for (const id of opened.slice(1, 4)) tool(chief, "mcp__crewbots__start_thread", { title: tasks.get(id)!.title, goal: "…" }, "Opened thread");
  say(chief, "Three threads, one each. React 19 will need a call from you on the charts library.");
  emit(chief, "flushed", { logged: 3 }); // a long chat, kept in shape: set down, then compacted
  emit(chief, "compacted", { trigger: "manual", before: 131_000, after: 8_400 });
  message(chief, "routine:standup", "[routine: standup]\n\nWrite the standup for what the crew did since Fri 23 Oct 09:00.");
  say(chief, "**Morning. Two things need you**\n- **React 19**: replace legacy-charts with recharts, or stay on React 18?\n- **Flaky login test**: reviewed and ready to merge.\n\n**Done**: `truncate`, merged after review.\n\n**Waiting**: the Spanish docs, until the Claude plan's limit resets.");
  message(chief, "user", "Merge the login fix. Then add CSV export to the reports page; exports should respect the filters.");
  tool(chief, "mcp__crewbots__start_thread", { title: "Add CSV export", goal: "…" }, "Opened thread");
  say(chief, "Merged. The CSV export is with the engineer now, and the reviewer and critic will check it before it comes to you.");
  done(chief);


  // What the engineer made for the release, shared under its messages: one of each kind of file.
  message(chatIds.engineer, "user", "Show me what you made for the export release.");
  tool(chatIds.engineer, "mcp__crewbots__share", { files: ["renders/hero-render.png", "exports-by-format.svg"] }, "Shared");
  emit(chatIds.engineer, "shared", { text: "The hero render for the release page, and exports by format over the last 30 days.", attachments: [files.render, files.chart] });
  emit(chatIds.engineer, "shared", { text: "The launch cut, and the chime for a finished export:", attachments: [files.cut, files.jingle] });
  emit(chatIds.engineer, "shared", { text: "", attachments: [files.notes, files.report] });
  emit(chatIds.engineer, "shared", { text: "The export code and its tests, and the scene the render came from.", attachments: [files.code, files.scene] });
  say(chatIds.engineer, "That's everything for the release: the notes are ready for you to read, and the page has the numbers.");
  done(chatIds.engineer);

  message(chatIds.engineer, "user", "Can you keep the test suite under 30 seconds?");
  say(chatIds.engineer, "It's at 18s now. The slowest file is `test_reports.py` (6s), mostly fixtures; I can trim that if it grows.");
  done(chatIds.engineer);

  // The engineer asks teammates: one answer is in, one is still coming.
  message(chatIds.engineer, "user", "Is the filtered export safe to ship on Friday?");
  const question = "Did the filtered export pass your break-it tests, with an empty filter too?";
  tool(chatIds.engineer, "mcp__crewbots__ask", { bot: "critic", question }, "Asked critic (t_ask0000001).");
  const answered = task("t_ask0000001", "critic", question, { kind: "ask", parent_id: chatIds.engineer, created_by: "engineer", workspace: null, result: "Yes. An empty filter exports every row, as the page shows, and 2,000 runs of the break-it script found nothing." });
  message(answered.id, "engineer", `engineer asks: ${question}`);
  say(answered.id, answered.result!);
  done(answered.id);
  message(chatIds.engineer, "crewbots", "Your question was answered.");
  say(chatIds.engineer, "The critic ran it 2,000 times with no failures, an empty filter included, so yes: it's safe for Friday. I've asked the reviewer whether release days are in our standards.");
  const rule = "Anything in team/standards.md about which days we release on?";
  tool(chatIds.engineer, "mcp__crewbots__ask", { bot: "reviewer", question: rule }, "Asked reviewer (t_ask0000002).");
  task("t_ask0000002", "reviewer", rule, { kind: "ask", parent_id: chatIds.engineer, created_by: "engineer", workspace: null, status: "running", reported: false });
  done(chatIds.engineer);
  emit(chatIds.reviewer, "text", { text: "PR acme/web#409 has waited on you since Friday; it's approved and its checks pass.", checkin: true, level: "question", proactive_id: 1 });
  message(chatIds.reviewer, "user", "What do you look for first?");
  const own = task("t_own0000001", "engineer", "Fuzz the CSV export's filters", { parent_id: chatIds.engineer, created_by: "crewbots", status: "running", reported: false });
  emit(own.id, "started_on_own", { why: "The export shipped without fuzz tests for unusual filters." });
  emit(own.id, "message", { from: "crewbots", text: "Write fuzz tests for the CSV export's filters, on a branch in your own worktree." });
  say(chatIds.reviewer, "That the change does what the task asked, with a test for each edge case. Then `team/standards.md`.");
  done(chatIds.reviewer);
}

// A group chat: the crew's own, where bots talk to each other in the open.
// A new, muted one nobody has written in yet. One where the bots went back and forth until they stopped. And an
// archived one.
type DemoRoom = Room & { stopped?: { after: number; waiting: string[]; owed?: string[] } | null };
export const rooms: DemoRoom[] = [
  { id: "r_crew000001", title: "Crew", lead: "chief", members: bots.map((b) => b.name), created_at: tick(), updated_at: tick(), crew: true },
  { id: "r_review0002", title: "Review desk", lead: "reviewer", members: ["reviewer", "critic"], created_at: tick(), updated_at: tick(), muted: true },
  { id: "r_naming0003", title: "Naming the export", lead: "engineer", members: ["engineer", "critic"], created_at: tick(), updated_at: tick(), stopped: { after: 4, waiting: ["engineer"], owed: ["engineer"] } },
  { id: "r_launch0004", title: "Launch prep", lead: "chief", members: ["chief", "engineer"], created_at: tick(), updated_at: tick(), archived_at: tick() },
];
export const roomMessages = new Map<string, { id: number; room_id: string; author: string; text: string; depth: number; at: string; attachments?: Attachment[] }[]>();
{
  let id = 1;
  const room = rooms[0].id;
  const said = (author: string, text: string, depth = 0) => ({ id: id++, room_id: room, author, text, depth, at: tick(1) });
  roomMessages.set(room, [
    said("user", "Morning, everyone. @engineer how's CSV export going?"),
    said("engineer", "Endpoint's in and its tests pass. Wiring the button now. @reviewer it'll be yours in a few minutes.", 1),
    said("reviewer", "Ready when you are. I'll check the filters reach the export.", 2),
    said("chief", "Thanks both. I'll propose it for merging once both reviews pass.", 1),
    { ...said("engineer", "The launch cut, for anyone who wants a look before Friday:", 1), attachments: [files.cut] },
    { ...said("user", "Here's the copy deck for it."), attachments: [files.notes] },
  ]);
  const naming = rooms[2].id;
  const argued = (author: string, text: string, depth: number) => ({ ...said(author, text, depth), room_id: naming });
  roomMessages.set(naming, [
    argued("user", "What should the export button say?", 0),
    argued("engineer", "\"Export CSV\". It says what you get. @critic agree?", 1),
    argued("critic", "\"Download\" is what people look for. @engineer", 2),
    argued("engineer", "Download what, though? The format matters when there's more than one. @critic", 3),
    argued("critic", "Then \"Download CSV\", and the menu can grow later. @engineer final?", 4),
  ]);
  roomMessages.set(rooms[3].id, [{ ...said("user", "Launch checklist, anyone?"), room_id: rooms[3].id }, { ...said("chief", "Done and shipped. Archiving this one.", 1), room_id: rooms[3].id }]);
}

// ?big=2000 makes the critic's chat that long, to see how a long chat opens.
{
  const big = Number(new URLSearchParams(globalThis.location?.search ?? "").get("big") ?? 0);
  for (let i = 0; i < big; i++) {
    const id = chatIds.critic;
    if (i % 5 === 0) emit(id, "message", { from: "user", text: `Can you look at issue ${i}? The **export** breaks on \`page ${i}\`.` });
    else if (i % 5 === 1) emit(id, "tool_call", { id: `big${i}`, name: "Bash", input: { command: `pytest -q tests/test_${i}.py` } });
    else if (i % 5 === 2) emit(id, "tool_result", { id: `big${i - 1}`, output: `${i} passed`, is_error: false });
    else if (i % 5 === 3) emit(id, "text", { text: `Fixed it:\n\n- the cursor resets on page ${i}\n- a test covers it` });
    else emit(id, "usage", { input_tokens: 1000, output_tokens: 50 });
  }
}
