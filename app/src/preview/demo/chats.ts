// Chats: one ongoing chat with each bot. The chief's opened the work above as threads, so they hang from it.

import { bots, done, emit, message, say, task, tasks, tick, tool } from "./make";

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
  message(chief, "routine:standup", "[routine: standup]\n\nWrite the standup for what the crew did since Fri 23 Oct 09:00.");
  say(chief, "**Morning. Two things need you**\n- **React 19**: replace legacy-charts with recharts, or stay on React 18?\n- **Flaky login test**: reviewed and ready to merge.\n\n**Done**: `truncate`, merged after review.\n\n**Waiting**: the Spanish docs, until the Claude plan's limit resets.");
  message(chief, "user", "Merge the login fix. Then add CSV export to the reports page; exports should respect the filters.");
  tool(chief, "mcp__crewbots__start_thread", { title: "Add CSV export", goal: "…" }, "Opened thread");
  say(chief, "Merged. The CSV export is with the engineer now, and the reviewer and critic will check it before it comes to you.");
  done(chief);


  message(chatIds.engineer, "user", "Can you keep the test suite under 30 seconds?");
  say(chatIds.engineer, "It's at 18s now. The slowest file is `test_reports.py` (6s), mostly fixtures; I can trim that if it grows.");
  done(chatIds.engineer);
  message(chatIds.reviewer, "user", "What do you look for first?");
  say(chatIds.reviewer, "That the change does what the task asked, with a test for each edge case. Then `team/standards.md`.");
  done(chatIds.reviewer);
}

// A group chat: the crew's own, where bots talk to each other in the open.
// And a new one nobody has written in yet.
export const rooms = [
  { id: "r_crew000001", title: "Crew", lead: "chief", members: bots.map((b) => b.name), created_at: tick(), updated_at: tick() },
  { id: "r_review0002", title: "Review desk", lead: "reviewer", members: ["reviewer", "critic"], created_at: tick(), updated_at: tick() },
];
export const roomMessages = new Map<string, { id: number; room_id: string; author: string; text: string; depth: number; at: string }[]>();
{
  let id = 1;
  const room = rooms[0].id;
  const said = (author: string, text: string, depth = 0) => ({ id: id++, room_id: room, author, text, depth, at: tick(1) });
  roomMessages.set(room, [
    said("user", "Morning, everyone. @engineer how's CSV export going?"),
    said("engineer", "Endpoint's in and its tests pass. Wiring the button now. @reviewer it'll be yours in a few minutes.", 1),
    said("reviewer", "Ready when you are. I'll check the filters reach the export.", 2),
    said("chief", "Thanks both. I'll propose it for merging once both reviews pass.", 1),
  ]);
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
