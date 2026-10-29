// What merged, and reverting it, for the preview's bridge: answered from the demo's merged work as the daemon would.

import type { Live as LiveMessage, StoredEvent } from "../../types";
import { crewState } from "../fixtures";
import { reviews, tasks } from "./make";

type Live = { publish: (message: LiveMessage) => void; say: (id: string, kind: string, data: Record<string, unknown>) => void };
const ok = (data: unknown) => ({ ok: true, status: 200, data });

export function landedRequest(method: string, pathname: string, parts: string[], allEvents: StoredEvent[], live: Live) {
  if (method === "GET" && pathname === "/landed") return ok({ items: landed(allEvents), seen_at: null });
  if (method === "POST" && pathname === "/landed/seen") {
    crewState.landed = 0;
    setTimeout(() => live.publish({ type: "crew", crew: { ...crewState } as never }), 0);
    return { ok: true, status: 204, data: null };
  }
  if (method === "POST" && parts[0] === "tasks" && parts[2] === "revert") {
    const task = tasks.get(parts[1]);
    if (task?.merge_state !== "merged") return { ok: false, status: 403, data: { detail: "Only merged work can be reverted" } };
    live.say(task.id, "reverted", { commit: task.merged_commit, revert_commit: "4c3b2a190f8e", by: "user" });
    return ok(task);
  }
  return null;
}

/** What merged, the latest first, as the daemon's /landed answers it. */
function landed(allEvents: StoredEvent[]) {
  return allEvents.filter((e) => e.kind === "merged").reverse().map((merged) => {
    const mine = allEvents.filter((e) => e.task_id === merged.task_id).reverse(); // the latest first
    const review = reviews.get(merged.task_id);
    return {
      task: tasks.get(merged.task_id)!, merged_at: merged.at, commit: merged.data.commit, into: merged.data.into,
      automatic: Boolean(merged.data.automatic), evidence: mine.find((e) => e.kind === "handoff")?.data.evidence ?? [],
      verdicts: (review?.reviews ?? []).filter((r) => r.verdict).map((r) => ({ bot: r.reviewer, verdict: r.verdict!, summary: r.summary ?? "" })),
      reverted: mine.find((e) => e.kind === "reverted")?.data ?? null,
    };
  });
}
