import { useState } from "react";
import type { Crew } from "../state";
import { doing, oneLine } from "../state";
import type { Task } from "../types";
import { ACTIVE } from "../types";
import { Icon } from "./Icon";

const STATUS: Record<Task["status"], string> = {
  queued: "Queued",
  running: "Working",
  idle: "Done",
  needs_input: "Has a question",
  failed: "Failed",
  cancelled: "Stopped",
  interrupted: "Interrupted",
  held: "Waiting to continue",
};

/** Work a bot was given, inside the conversation it came from: who, what, where it is, and its review. */
export function WorkCard({ crew, task, onOpen }: { crew: Crew; task: Task; onOpen: (id: string) => void }) {
  const below = crew.family(task.id).find((t) => ACTIVE.includes(t.status) && !t.review_of);
  const active = ACTIVE.includes(task.status) || Boolean(below);
  const status = ACTIVE.includes(task.status) || !below ? task.status : below.status;
  const reviews = (crew.children.get(task.parent_id ?? "") ?? []).filter((t) => t.review_of === task.id);
  const versions = [...new Set(reviews.flatMap((r) => r.review_version == null ? [] : [r.review_version]))].sort((a, b) => a - b);
  const round = versions.length;
  const latest = reviews.filter((r) => r.review_version != null && r.review_version === versions.at(-1));
  const verdicts = crew.verdicts.get(task.id) ?? [];
  const verdictOf = (review: Task) => verdicts.filter((v) => v.data.task_id === review.id).at(-1)?.data.verdict as string | undefined;
  const line = active ? doing(crew.latest.get((ACTIVE.includes(task.status) ? task : below ?? task).id)) : oneLine(task.result ?? task.error ?? "", 200);

  return (
    <div className={`work ${task.status}`} onClick={() => onOpen(task.id)} role="button" tabIndex={0}>
      <div className="work-head">
        <span className="work-bot">
          <Icon name="message" size={13} />
          {task.created_by === "crewbots" ? "Started on its own" : task.bot === crew.tasks.get(task.parent_id ?? "")?.bot ? "Thread" : `Thread · ${task.bot}`}
        </span>
        <span className="grow" />
        {task.created_by === "crewbots" && active && (
          <button
            className="button small work-stop"
            onClick={(e) => {
              e.stopPropagation();
              crew.cancel(task.id).catch(() => undefined);
            }}
            title="Stop the work it started on its own"
          >
            Stop
          </button>
        )}
        <span className={`work-status ${status}`}>
          {active && <span className="working-dot small" />}
          {crew.asks.has(task.id) ? "Asks your permission" : STATUS[status]}
        </span>
      </div>
      <div className="work-title">{oneLine(task.title, 160)}</div>
      {line && <div className={`work-line ${active ? "shimmer" : ""}`}>{line}</div>}
      {(latest.length > 0 || task.merge_state) && (
        <div className="work-foot" onClick={(e) => e.stopPropagation()}>
          {latest.map((review) => {
            const v = verdictOf(review);
            return (
              <span key={review.id} className={`review-chip ${v ?? "pending"}`} onClick={() => onOpen(review.id)} title={`Open ${review.bot}'s review`}>
                {v === "pass" ? <Icon name="check" size={12} /> : v === "fail" ? <Icon name="x" size={12} /> : <span className="working-dot small" />}
                {review.bot}
              </span>
            );
          })}
          {round > 0 && <span className="faint">round {round} of 3</span>}
          <span className="grow" />
          <MergeButton crew={crew} task={task} />
        </div>
      )}
    </div>
  );
}

export function MergeButton({ crew, task }: { crew: Crew; task: Task }) {
  const [busy, setBusy] = useState(false);
  if (task.merge_state === "merged") {
    return (
      <span className="merged">
        <Icon name="merge" size={13} /> Merged
      </span>
    );
  }
  if (task.merge_state !== "proposed") return null;
  const merge = async () => {
    if (!window.confirm(`Merge ${task.branch} into ${task.repo ?? "your checkout"}?`)) return;
    setBusy(true);
    await crew.merge(task.id, false).catch(() => undefined);
    setBusy(false);
  };
  return (
    <button className="button primary small" disabled={busy || ACTIVE.includes(task.status)} onClick={merge}>
      <Icon name="merge" size={13} />
      {busy ? "Merging…" : "Merge"}
    </button>
  );
}
