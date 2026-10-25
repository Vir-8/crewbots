import type { ReviewState, Task } from "../types";
import { Linked, StatusDot } from "./bits";

export function ReviewPanel(props: {
  task: Task;
  review: ReviewState | null;
  tasks: Map<string, Task>;
  onSelect: (id: string) => void;
}) {
  const { review } = props;
  if (props.task.review_of) {
    return (
      <div className="pad">
        <p>
          This task is a review of <Linked text={props.task.review_of} onSelect={props.onSelect} /> (version{" "}
          {props.task.review_version}). Its verdict is in the transcript.
        </p>
      </div>
    );
  }
  if (!review || review.round === 0) return <p className="muted pad">Not reviewed yet.</p>;

  const verdict = review.approved
    ? { label: "Approved", tone: "good", text: "Every review of the current version passed." }
    : review.out_of_rounds
      ? { label: "Out of rounds", tone: "bad", text: "It failed review in every round. Your call: merge anyway, or drop it." }
      : review.failed
        ? { label: "Changes requested", tone: "warn", text: "The owner gets the findings and its next version is reviewed again." }
        : review.reviews.some((r) => r.status === "running" || r.status === "queued")
          ? { label: "In review", tone: "", text: "Reviews of this version are still running." }
          : { label: "Out of date", tone: "warn", text: "The work changed after its last review." };

  return (
    <div className="review pad">
      <div className={`review-head ${verdict.tone}`}>
        <div className="review-verdict">{verdict.label}</div>
        <div className="muted">
          Round {review.round} of {review.max_rounds} · reviewed version {review.version} · {verdict.text}
        </div>
        <Rounds round={review.round} max={review.max_rounds} />
      </div>
      {review.reviews.map((r) => (
        <div key={r.task_id} className={`card verdict ${r.verdict ?? "none"}`}>
          <div className="card-title">
            <StatusDot status={r.status} /> {r.reviewer}: {r.verdict ? r.verdict.toUpperCase() : "no verdict yet"}{" "}
            <a className="task-link small" onClick={() => props.onSelect(r.task_id)}>
              open
            </a>
          </div>
          {r.summary && <div>{r.summary}</div>}
          {r.findings.length > 0 && (
            <ul className="findings">
              {r.findings.map((f, i) => (
                <li key={i}>
                  <span className="mono">{f.location}</span> {f.problem}
                  {f.scenario && <div className="muted small">Scenario: {f.scenario}</div>}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

function Rounds({ round, max }: { round: number; max: number }) {
  return (
    <div className="rounds">
      {Array.from({ length: max }, (_, i) => (
        <span key={i} className={`round ${i < round ? "used" : ""}`} />
      ))}
    </div>
  );
}
