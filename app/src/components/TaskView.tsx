import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import type { ReviewState, StoredEvent, Task } from "../types";
import { ACTIVE } from "../types";
import { ago, Inline, Linked, StatusDot, StatusPill, tokens } from "./bits";
import { DiffView } from "./DiffView";
import { ReviewPanel } from "./ReviewPanel";
import { Transcript } from "./Transcript";

type Tab = "transcript" | "review" | "changes";

export function TaskView(props: {
  task: Task;
  tasks: Map<string, Task>;
  delegated: Task[];
  events: StoredEvent[] | undefined;
  onSelect: (id: string) => void;
  onError: (message: string) => void;
}) {
  const { task } = props;
  const [tab, setTab] = useState<Tab>("transcript");
  const [review, setReview] = useState<ReviewState | null>(null);
  const reviewed = task.review_of ? props.tasks.get(task.review_of) : undefined;
  const parent = task.parent_id ? props.tasks.get(task.parent_id) : undefined;
  const active = ACTIVE.includes(task.status);

  // The review state changes with every verdict and every finished run, which all arrive as task updates.
  useEffect(() => {
    api.review(task.id).then(setReview, () => setReview(null));
  }, [task.id, task.updated_at, props.delegated.map((t) => t.updated_at).join()]);

  return (
    <section className="task-view">
      <header className="task-head">
        <div className="crumbs">
          {parent && (
            <>
              <a className="task-link" onClick={() => props.onSelect(parent.id)}>
                {parent.bot}: {parent.title}
              </a>
              <span className="muted"> › </span>
            </>
          )}
        </div>
        <h1>
          <Inline text={task.title} onSelect={props.onSelect} />
        </h1>
        <div className="meta">
          <StatusPill status={task.status} />
          <span className="chip">{task.bot}</span>
          {task.created_by !== "user" && <span className="muted">from {task.created_by}</span>}
          {reviewed && (
            <span className="muted">
              reviews <Linked text={reviewed.id} onSelect={props.onSelect} />
            </span>
          )}
          {task.branch && (
            <span className="mono muted clickable" title="Show in Finder" onClick={() => bridge.reveal(task.worktree_path!)}>
              {task.branch}
            </span>
          )}
          <span className="muted">{tokens(task.input_tokens + task.output_tokens)} tokens</span>
          <span className="muted">updated {ago(task.updated_at)}</span>
          <span className="spacer" />
          {active && (
            <button className="ghost" onClick={() => api.cancel(task.id).catch((e) => props.onError(String(e)))}>
              Stop
            </button>
          )}
          <MergeButton task={task} review={review} onError={props.onError} />
        </div>
        {task.error && task.status === "failed" && <div className="banner error">{task.error}</div>}
        {props.delegated.length > 0 && (
          <div className="delegated">
            {props.delegated.map((kid) => (
              <button key={kid.id} className="kid" onClick={() => props.onSelect(kid.id)} title={kid.title}>
                <StatusDot status={kid.status} />
                <b>{kid.bot}</b> <span className="kid-title">{kid.title}</span>
                {kid.merge_state && <span className={`badge ${kid.merge_state === "merged" ? "merged" : "ready"}`}>{kid.merge_state}</span>}
              </button>
            ))}
          </div>
        )}
        <nav className="tabs">
          <TabButton tab="transcript" current={tab} onClick={setTab} label="Transcript" />
          {(review?.round || task.review_of) && <TabButton tab="review" current={tab} onClick={setTab} label="Review" />}
          {task.worktree_path && <TabButton tab="changes" current={tab} onClick={setTab} label="Changes" />}
        </nav>
      </header>

      <div className="task-body">
        {tab === "transcript" && <Transcript events={props.events} onSelect={props.onSelect} />}
        {tab === "review" && <ReviewPanel task={task} review={review} tasks={props.tasks} onSelect={props.onSelect} />}
        {tab === "changes" && <DiffView task={task} />}
      </div>

      <Composer task={task} onError={props.onError} />
    </section>
  );
}

function TabButton(props: { tab: Tab; current: Tab; label: string; onClick: (tab: Tab) => void }) {
  return (
    <button className={`tab ${props.current === props.tab ? "active" : ""}`} onClick={() => props.onClick(props.tab)}>
      {props.label}
    </button>
  );
}

function MergeButton({ task, review, onError }: { task: Task; review: ReviewState | null; onError: (m: string) => void }) {
  const [busy, setBusy] = useState(false);
  if (!task.worktree_path || task.merge_state === "merged") {
    return task.merged_commit ? <span className="badge merged">merged {task.merged_commit.slice(0, 8)}</span> : null;
  }
  const approved = Boolean(review?.approved);
  const merge = async () => {
    const where = task.repo ?? "your checkout";
    const question = approved
      ? `Merge ${task.branch} into the current branch of ${where}?`
      : `${task.branch} is not approved: no passing review of its current version.\n\nMerge it into ${where} anyway?`;
    if (!window.confirm(question)) return;
    setBusy(true);
    try {
      await api.merge(task.id, !approved);
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      className={approved ? "primary" : "ghost"}
      disabled={busy || ACTIVE.includes(task.status)}
      onClick={merge}
      title={approved ? "Every review of the current version passed" : "Not approved yet"}
    >
      {busy ? "Merging…" : approved ? "Merge" : "Merge anyway…"}
    </button>
  );
}

function Composer({ task, onError }: { task: Task; onError: (message: string) => void }) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const asking = task.status === "needs_input";
  const send = async () => {
    if (!text.trim()) return;
    setSending(true);
    try {
      await api.send(task.id, text.trim());
      setText("");
    } catch (e) {
      onError(String(e));
    } finally {
      setSending(false);
    }
  };
  return (
    <footer className={`composer ${asking ? "asking" : ""}`}>
      <textarea
        value={text}
        placeholder={
          asking ? `${task.bot} is waiting on your answer…` : ACTIVE.includes(task.status)
            ? `Message ${task.bot}; it reads this on its next turn`
            : `Follow up with ${task.bot}`
        }
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send();
        }}
        rows={2}
      />
      <button className="primary" disabled={sending || !text.trim()} onClick={send} title="⌘↩">
        Send
      </button>
    </footer>
  );
}
