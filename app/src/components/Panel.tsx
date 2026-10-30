import { type ReactNode, useEffect, useState } from "react";
import { api, bridge } from "../api";
import type { Crew } from "../state";
import { oneLine } from "../activityText";
import type { ReviewState, Task } from "../types";
import { ACTIVE } from "../types";
import { crewCommands } from "./CommandPreview";
import { Composer } from "./Composer";
import { DiffView } from "./DiffView";
import { FindBar, useFind } from "./Find";
import { Icon } from "./Icon";
import { ReviewPanel } from "./ReviewPanel";
import { Thread } from "./Thread";
import { MergeButton } from "./WorkCard";
import "../styles/panels.css";

type Tab = "activity" | "changes" | "review";

/** One delegated task, beside the conversation it belongs to. */
export function Panel(props: { crew: Crew; task: Task; onOpen: (id: string) => void; onClose: () => void; edge?: ReactNode }) {
  const { crew, task } = props;
  const [tab, setTab] = useState<Tab>("activity");
  const [review, setReview] = useState<ReviewState | null>(null);
  const bot = crew.bots.find((b) => b.name === task.bot);
  const reviewOf = task.review_of ? crew.tasks.get(task.review_of) : undefined;
  const find = useFind("panel", () => setTab("activity")); // after a click in the thread, ⌘F finds in it

  useEffect(() => {
    api.review(task.id).then(setReview, () => setReview(null));
  }, [task.id, task.updated_at, crew.recent.length]);
  useEffect(() => setTab("activity"), [task.id]);

  const tabs: [Tab, string][] = [["activity", "Activity"]];
  if (task.worktree_path) tabs.push(["changes", "Changes"]);
  if (review?.round) tabs.push(["review", "Review"]);

  return (
    <aside className="panel">
      {props.edge}
      <header className="panel-head" data-tauri-drag-region="deep">
        <div className="panel-top">
          <div className="panel-who">
            <span className="panel-bot">{task.bot}</span>
            <span className="faint">{bot?.title}</span>
          </div>
          <button className="icon-button" onClick={props.onClose} title="Close">
            <Icon name="x" size={15} />
          </button>
        </div>
        <h2 className="panel-title">{oneLine(task.title, 200)}</h2>
        <div className="panel-meta">
          {reviewOf && (
            <button className="text-button" onClick={() => props.onOpen(reviewOf.id)}>
              Reviews {reviewOf.bot}'s work
            </button>
          )}
          {task.branch && (
            <button className="meta-chip" onClick={() => bridge.reveal(task.worktree_path!)} title="Show in Finder">
              <Icon name="branch" size={12} />
              {task.branch}
            </button>
          )}
          <span className="grow" />
          {ACTIVE.includes(task.status) && (
            <button className="button small" onClick={() => crew.cancel(task.id)}>
              Stop
            </button>
          )}
          <MergeButton crew={crew} task={task} />
        </div>
        {tabs.length > 1 && (
          <nav className="segmented">
            {tabs.map(([key, label]) => (
              <button key={key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
                {label}
              </button>
            ))}
          </nav>
        )}
      </header>
      <div className="panel-body">
        {find.finding && tab === "activity" && <FindBar find={find} placeholder="Find in this thread" />}
        {tab === "activity" && <Thread crew={crew} task={task} onOpen={props.onOpen} compact find={{ query: find.lookFor, current: find.current, onFound: find.setFound }} />}
        {tab === "changes" && <DiffView task={task} />}
        {tab === "review" && <ReviewPanel task={task} review={review} tasks={crew.tasks} onSelect={props.onOpen} />}
      </div>
      <div className="panel-composer">
        <Composer
          key={task.id}
          draft={`task:${task.id}`}
          placeholder={task.status === "needs_input" ? `Answer ${task.bot}` : `Message ${task.bot}`}
          asking={task.status === "needs_input"}
          mentions={crew.bots.map((b) => b.name)}
          onSend={(text, files) => crew.send(task.id, text, files)}
          task={task.id}
          actions={{ stop: ACTIVE.includes(task.status) ? () => crew.cancel(task.id) : undefined, ...crewCommands(crew) }}
        />
      </div>
    </aside>
  );
}
