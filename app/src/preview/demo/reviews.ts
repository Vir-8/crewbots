// A private review of acme/web#412: two reviewers on their own, then the lead's one review and its drafted comment.

import type { Task } from "../../types";
import { done, emit, message, say, task, tasks, tool } from "./make";

export const pullReviews: Task[] = [];
{
  const base = { kind: "review" as const, pr: "acme/web#412", workspace: "~/.crewbots/reviews/checkouts/acme-web-412-abc", title: "Review acme/web#412: Move the export button into the reports toolbar" };
  const lead = task("t_pr412lead", "reviewer:claude:sonnet:high", base.title, base);
  const kids = [task("t_pr412son", "reviewer:claude:sonnet:high", base.title, { ...base, parent_id: lead.id }), task("t_pr412gpt", "reviewer:codex:default:high", base.title, { ...base, parent_id: lead.id })];
  message(lead.id, "user", "Review https://github.com/acme/web/pull/412");
  emit(lead.id, "notice", { message: "Getting acme/web's code. The first review of a repository takes about a minute; later ones, a few seconds." });
  for (const k of kids) {
    message(k.id, "user", "Review https://github.com/acme/web/pull/412.\n\nYour working directory is the repository, checked out at the pull request's head (abc123def456).");
    tool(k.id, "mcp__crewbots__pr_overview", {}, "# Move the export button into the reports toolbar");
    tool(k.id, "mcp__crewbots__pr_diff", {}, "Part 1 of 1.");
    tool(k.id, k.id === "t_pr412son" ? "Grep" : "shell", k.id === "t_pr412son" ? { pattern: "export_csv" } : { command: "rg -n 'filename=' server/" }, "server/reports.py:64");
    say(k.id, "**P1** `server/reports.py:64`: `filename` is fixed, so two exports in a row overwrite each other in the browser's downloads.\n\nOverall risk: low. I didn't run anything.");
    done(k.id);
  }
  message(lead.id, "crewbots", "The reviewers have finished.");
  tool(lead.id, "mcp__crewbots__pr_diff", { path: "server/reports.py" }, "diff --git a/server/reports.py b/server/reports.py");
  tool(lead.id, "Read", { file_path: "server/reports.py" });
  tool(lead.id, "mcp__crewbots__draft_comment", { path: "server/reports.py", line: 64, body: "…" }, "Drafted comment 1 on server/reports.py:64.");
  say(lead.id, "One issue, not blocking. I drafted a comment for it.\n\n**Findings**\n\n1. **P1** `server/reports.py:64`\n   - The export's filename is fixed, so a second export overwrites the first in the browser's downloads.\n   - Both reviewers found it.\n\n2. **P2** `web/src/reports/Toolbar.tsx:40` (no comment drafted)\n   - The button has no busy state, so a double click starts two exports.\n   - Sonnet found this; GPT didn't.\n\n**Overall impact:** small. The button moves; the endpoint's behaviour is unchanged.\n\n**Couldn't check:** how Safari names repeated downloads. Nothing was run.");
  done(lead.id);
  pullReviews.push(tasks.get(lead.id)!, ...kids.map((k) => tasks.get(k.id)!));
}
