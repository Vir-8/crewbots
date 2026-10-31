import { useEffect, useRef, useState } from "react";
import { api, bridge, errorText } from "../api";
import type { Crew } from "../state";
import { doing, oneLine } from "../activityText";
import type { Task } from "../types";
import { ACTIVE } from "../types";
import { labelOf } from "./Avatar";
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
  const [cap, setCap] = useState<{ task: string; max: number } | null>(null);
  useEffect(() => {
    if (task.merge_state) crew.ensureEvents(task.id);
  }, [crew.ensureEvents, task.id, task.merge_state]);
  const proposal = (crew.events.get(task.id) ?? []).filter((event) => event.kind === "merge_proposed").at(-1);
  const files: string[] | undefined = proposal?.data.files;
  const merged = (crew.events.get(task.id) ?? []).filter((event) => event.kind === "merged").at(-1);
  const leftovers: string[] | undefined = merged?.data.leftover_files;
  const checkError: string | undefined = merged?.data.leftover_check_error;
  useEffect(() => {
    let current = true;
    if (round) api.review(task.id).then((review) => { if (current) setCap({ task: task.id, max: review.max_rounds }); }, () => { if (current) setCap(null); });
    return () => { current = false; };
  }, [task.id, round]);
  const latest = reviews.filter((r) => r.review_version != null && r.review_version === versions.at(-1));
  const verdicts = crew.verdicts.get(task.id) ?? [];
  const verdictOf = (review: Task) => verdicts.filter((v) => v.data.task_id === review.id).at(-1)?.data.verdict as string | undefined;
  const line = active ? doing(crew.latest.get((ACTIVE.includes(task.status) ? task : below ?? task).id)) : oneLine(task.result ?? task.error ?? "", 200);

  return (
    <div className={`work ${task.status}`} onClick={() => onOpen(task.id)} role="button" tabIndex={0}>
      <div className="work-head">
        <span className="work-bot">
          <Icon name="message" size={13} />
          {task.created_by === "crewbots" ? "Started on its own" : task.bot === crew.tasks.get(task.parent_id ?? "")?.bot ? "Thread" : `Thread · ${labelOf(task.bot)}`}
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
      {task.merge_state === "proposed" && files && (
        <div className="work-files"><div>Changed files</div><ul>{files.map((file) => <li key={file}><code>{file}</code></li>)}</ul></div>
      )}
      {task.merge_state === "merged" && Boolean(leftovers?.length) && (
        <div className="work-files"><div>Saved after the merge started, still uncommitted:</div><ul>{leftovers!.map((file) => <li key={file}><code>{file}</code></li>)}</ul></div>
      )}
      {task.merge_state === "merged" && checkError && <div className="work-files">Could not check for later saves: {checkError}</div>}
      {(latest.length > 0 || task.merge_state) && (
        <div className="work-foot" onClick={(e) => e.stopPropagation()}>
          {latest.map((review) => {
            const v = verdictOf(review);
            return (
              <span key={review.id} className={`review-chip ${v ?? "pending"}`} onClick={() => onOpen(review.id)} title={`Open ${labelOf(review.bot)}'s review`}>
                {v === "pass" ? <Icon name="check" size={12} /> : v === "fail" ? <Icon name="x" size={12} /> : <span className="working-dot small" />}
                {labelOf(review.bot)}
              </span>
            );
          })}
          {round > 0 && <span className="faint">round {round}{cap?.task === task.id ? ` of ${cap.max}` : ""}</span>}
          <span className="grow" />
          <MergeButton crew={crew} task={task} />
        </div>
      )}
    </div>
  );
}

export function MergeButton({ crew, task }: { crew: Crew; task: Task }) {
  const [busy, setBusy] = useState(false);
  const [withdrawing, setWithdrawing] = useState(false);
  const [reason, setReason] = useState("");
  const [failure, setFailure] = useState<string | null>(null);
  const [alreadyOn, setAlreadyOn] = useState<string | null>(null);
  // Main can move under a proposal: ask again when the task has news and when the window comes back.
  const news = crew.events.get(task.id)?.length ?? 0;
  let merges = 0; // another task merging moves main, so every proposal is asked about again
  for (const other of crew.tasks.values()) if (other.merge_state === "merged") merges++;
  const asked = useRef(0);
  useEffect(() => {
    let current = true;
    if (task.merge_state !== "proposed") {
      setAlreadyOn(null);
      return;
    }
    const check = () => {
      const mine = ++asked.current; // an answer to an older question never replaces a newer one
      api.mergeStatus(task.id).then((status) => { if (current && mine === asked.current) setAlreadyOn(status.already_on); }, () => undefined);
    };
    check();
    window.addEventListener("focus", check);
    return () => { current = false; window.removeEventListener("focus", check); };
  }, [task.id, task.merge_state, news, merges]);
  useEffect(() => {
    if (!withdrawing || busy) return;
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.isComposing) { setWithdrawing(false); setReason(""); setFailure(null); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [withdrawing, busy]);
  if (task.merge_state === "merged") {
    return (
      <span className="merged">
        <Icon name="merge" size={13} /> Merged
      </span>
    );
  }
  if (task.merge_state === "dropped") {
    const dropped = (crew.events.get(task.id) ?? []).filter((event) => event.kind === "dropped").at(-1);
    return (
      <span className="merged dropped" title={dropped?.data.reason}>
        <Icon name="x" size={13} /> Dropped
      </span>
    );
  }
  if (task.merge_state !== "proposed") return null;
  const merge = async () => {
    if (!(await bridge.confirm(`Merge ${task.branch} into ${task.repo ?? "your checkout"}?`, "Merge"))) return;
    setBusy(true);
    await crew.merge(task.id, false).catch(() => undefined);
    setBusy(false);
  };
  const withdraw = async () => {
    if (!reason.trim() || busy) return;
    setBusy(true);
    setFailure(null);
    try {
      await crew.withdrawMerge(task.id, reason.trim());
      setWithdrawing(false);
      setReason("");
    } catch (e) {
      // Keep the reason for another try, and say why it failed where the user is looking.
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      {alreadyOn && <span className="merged"><Icon name="merge" size={13} /> Already on {alreadyOn}</span>}
      <button className="button small" disabled={busy} onClick={() => setWithdrawing(true)}>Withdraw</button>
      {!alreadyOn && (
        <button className="button primary small" disabled={busy || ACTIVE.includes(task.status)} onClick={merge}>
          <Icon name="merge" size={13} />
          {busy ? "Merging…" : "Merge"}
        </button>
      )}
      {withdrawing && (
        <div className="dialog-backdrop" onClick={(e) => { e.stopPropagation(); if (!busy) setWithdrawing(false); }}>
          <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="withdraw-title" onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); void withdraw(); }}>
            <h2 id="withdraw-title">Withdraw merge proposal</h2>
            <label className="dialog-label" htmlFor="withdraw-reason">Reason</label>
            <input id="withdraw-reason" className="dialog-input" value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
            {failure && <p role="alert" className="error-text small">{failure}</p>}
            <div className="dialog-actions">
              <button type="button" className="button" disabled={busy} onClick={() => { setWithdrawing(false); setReason(""); setFailure(null); }}>Cancel</button>
              <button type="submit" className="button primary" disabled={busy || !reason.trim()}>{busy ? "Withdrawing…" : "Withdraw"}</button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
