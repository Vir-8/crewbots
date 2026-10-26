import type { ReviewState, Task } from "../types";
import { Icon } from "./Icon";
import "../styles/panels.css";

/** Where a piece of work stands in review: the round, and each reviewer's verdict on the current version. */
export function ReviewPanel(props: { task: Task; review: ReviewState | null; tasks: Map<string, Task>; onSelect: (id: string) => void }) {
  const { review } = props;
  if (!review || review.round === 0) return <p className="review-empty faint">Not reviewed yet.</p>;

  const pending = review.reviews.some((r) => r.status === "running" || r.status === "queued");
  const state = review.approved
    ? { label: "Approved", tone: "good", text: "Every review of the current version passed." }
    : review.out_of_rounds
      ? { label: "Out of review rounds", tone: "bad", text: "It failed in every round. It's your call: merge anyway, or drop it." }
      : review.failed
        ? { label: "Changes requested", tone: "warn", text: "The owner gets the findings, and its next version is reviewed again." }
        : pending
          ? { label: "In review", tone: "", text: "Reviews of this version are still running." }
          : { label: "Out of date", tone: "warn", text: "It changed after its last review." };

  return (
    <div className="review">
      <div className={`review-head ${state.tone}`}>
        <div className="review-verdict">{state.label}</div>
        <div className="faint">{state.text}</div>
        <div className="rounds" title={`Round ${review.round} of ${review.max_rounds}`}>
          {Array.from({ length: review.max_rounds }, (_, i) => (
            <span key={i} className={`round ${i < review.round ? "used" : ""}`} />
          ))}
          <span className="faint rounds-label">
            Round {review.round} of {review.max_rounds}
          </span>
        </div>
      </div>
      {review.reviews.map((r) => (
        <button key={r.task_id} className={`verdict ${r.verdict ?? "pending"}`} onClick={() => props.onSelect(r.task_id)}>
          <div className="verdict-head">
            {r.verdict === "pass" ? <Icon name="check" size={14} /> : r.verdict === "fail" ? <Icon name="x" size={14} /> : <span className="working-dot small" />}
            <span>
              {r.reviewer} {r.verdict === "pass" ? "passed" : r.verdict === "fail" ? "requested changes" : "is reviewing"}
            </span>
          </div>
          {r.summary && <div className="verdict-text">{r.summary}</div>}
          {r.findings.length > 0 && (
            <ul className="findings">
              {r.findings.map((f, i) => (
                <li key={i}>
                  <code>{f.location}</code> {f.problem}
                  {f.scenario && <div className="faint">When {f.scenario}</div>}
                </li>
              ))}
            </ul>
          )}
        </button>
      ))}
    </div>
  );
}
