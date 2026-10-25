import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import type { Crew } from "../state";
import type { ReviewState, Task } from "../types";
import { ACTIVE } from "../types";
import { Avatar } from "./Avatar";
import { ago, Inline, StatusPill, tokens } from "./bits";
import { DiffView } from "./DiffView";
import { ReviewPanel } from "./ReviewPanel";
import { Transcript } from "./Transcript";

type Tab = "transcript" | "review" | "changes";

/** One task, as every layout shows it: who, what, where it stands, the transcript, review, diff, and what you can do. */
export function TaskDetail(props: { crew: Crew; task: Task; onSelect: (id: string) => void; onClose?: () => void }) {
  const { crew, task } = props;
  const [tab, setTab] = useState<Tab>("transcript");
  const [review, setReview] = useState<ReviewState | null>(null);
  const bot = crew.bots.find((b) => b.name === task.bot);
  const parent = task.parent_id ? crew.tasks.get(task.parent_id) : undefined;
  const kids = crew.children.get(task.id) ?? [];

  useEffect(() => crew.ensureEvents(task.id), [task.id, crew.ensureEvents]);
  useEffect(() => {
    api.review(task.id).then(setReview, () => setReview(null));
  }, [task.id, task.updated_at, kids.map((k) => k.updated_at).join()]);

  return (
    <section className="detail">
      <header className="detail-head">
        <div className="detail-top">
          <Avatar name={task.bot} bot={bot} status={task.status} size={34} />
          <div className="detail-who">
            <div className="detail-bot">
              {task.bot}
              <span className="muted"> · {bot?.title ?? ""}</span>
              {parent && (
                <span className="muted">
                  {" "}
                  for{" "}
                  <a className="link" onClick={() => props.onSelect(parent.id)}>
                    {parent.bot}
                  </a>
                </span>
              )}
            </div>
            <h2 className="detail-title">
              <Inline text={task.title} onSelect={props.onSelect} />
            </h2>
          </div>
          {props.onClose && (
            <button className="icon" onClick={props.onClose} title="Close">
              ✕
            </button>
          )}
        </div>
        <div className="detail-meta">
          <StatusPill status={task.status} />
          {task.branch && (
            <span className="mono soft clickable" onClick={() => bridge.reveal(task.worktree_path!)} title="Show in Finder">
              {task.branch}
            </span>
          )}
          <span className="soft">{tokens(task.input_tokens + task.output_tokens)} tokens</span>
          <span className="soft">{ago(task.updated_at)}</span>
          <span className="grow" />
          {ACTIVE.includes(task.status) && (
            <button className="quiet" onClick={() => crew.cancel(task.id)}>
              Stop
            </button>
          )}
          <MergeAction crew={crew} task={task} review={review} />
        </div>
        <nav className="tabs">
          <TabButton tab="transcript" current={tab} onClick={setTab} label="Activity" />
          {(review?.round || task.review_of) ? <TabButton tab="review" current={tab} onClick={setTab} label="Review" /> : null}
          {task.worktree_path && <TabButton tab="changes" current={tab} onClick={setTab} label="Changes" />}
        </nav>
      </header>
      <div className="detail-body">
        {tab === "transcript" && <Transcript events={crew.events.get(task.id)} onSelect={props.onSelect} />}
        {tab === "review" && <ReviewPanel task={task} review={review} tasks={crew.tasks} onSelect={props.onSelect} />}
        {tab === "changes" && <DiffView task={task} />}
      </div>
      <Composer crew={crew} task={task} />
    </section>
  );
}

function TabButton(props: { tab: Tab; current: Tab; label: string; onClick: (tab: Tab) => void }) {
  return (
    <button className={`tab ${props.current === props.tab ? "on" : ""}`} onClick={() => props.onClick(props.tab)}>
      {props.label}
    </button>
  );
}

export function MergeAction({ crew, task, review }: { crew: Crew; task: Task; review: ReviewState | null }) {
  const [busy, setBusy] = useState(false);
  if (task.merge_state === "merged") return <span className="tag good">Merged {task.merged_commit?.slice(0, 7)}</span>;
  if (!task.worktree_path) return null;
  const approved = Boolean(review?.approved) || task.merge_state === "proposed";
  const merge = async () => {
    const question = approved
      ? `Merge ${task.branch} into ${task.repo ?? "your checkout"}?`
      : `${task.branch} has no passing review of its current version.\n\nMerge it anyway?`;
    if (!window.confirm(question)) return;
    setBusy(true);
    await crew.merge(task.id, !review?.approved).catch(() => undefined);
    setBusy(false);
  };
  return (
    <button className={approved ? "primary" : "quiet"} disabled={busy || ACTIVE.includes(task.status)} onClick={merge}>
      {busy ? "Merging…" : approved ? "Merge" : "Merge anyway"}
    </button>
  );
}

export function Composer({ crew, task, placeholder }: { crew: Crew; task: Task; placeholder?: string }) {
  const [text, setText] = useState("");
  const asking = task.status === "needs_input";
  const send = async () => {
    const value = text.trim();
    if (!value) return;
    setText("");
    await crew.send(task.id, value).catch(() => setText(value));
  };
  return (
    <footer className={`composer ${asking ? "asking" : ""}`}>
      <textarea
        value={text}
        rows={1}
        placeholder={placeholder ?? (asking ? `${task.bot} is waiting on your answer` : `Message ${task.bot}`)}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            send();
          }
        }}
      />
      <button className="send" disabled={!text.trim()} onClick={send} title="Send (↩)">
        ↑
      </button>
    </footer>
  );
}
