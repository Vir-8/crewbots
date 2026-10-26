import { useState } from "react";
import { api } from "../../api";
import { useDraft } from "../../drafts";
import { Icon } from "../../components/Icon";
import { MentionArea } from "../../components/Mentions";
import type { Pull, Submitted } from "../../types";
import { people } from "./shared";
type Verdict = "COMMENT" | "APPROVE" | "REQUEST_CHANGES";
const VERDICTS: [Verdict, string, string][] = [
  ["COMMENT", "Comment", "Feedback, without approving or holding it up"],
  ["APPROVE", "Approve", "Good to merge"],
  ["REQUEST_CHANGES", "Request changes", "Must be addressed before it's merged"],
];

/** Your review, posted to GitHub under your name: the only thing crewbots ever submits, and only from here. */
export function SubmitPanel({ pull, drafts, yours, onClose, onDone }: { pull: Pull; drafts: number; yours: boolean; onClose: () => void; onDone: (done: Submitted) => void }) {
  const [verdict, setVerdict] = useState<Verdict>("COMMENT");
  const [body, setBody, forgetBody] = useDraft(`review:${pull.repo}#${pull.number}`);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needsBody = (verdict === "REQUEST_CHANGES" && !body.trim()) || (verdict === "COMMENT" && !body.trim() && drafts === 0);
  return (
    <div className="submit-panel" onClick={(e) => e.stopPropagation()}>
      <div className="command-panel-head">
        <span className="command-panel-title">Submit your review</span>
        <span className="grow" />
        <button className="icon-button" onClick={onClose} title="Close">
          <Icon name="x" size={13} />
        </button>
      </div>
      <MentionArea rows={4} autoFocus value={body} onChange={setBody} people={people(pull)} placeholder="Leave a summary (optional when approving)" />
      <div className="verdicts">
        {VERDICTS.map(([value, label, hint]) => {
          const blocked = yours && value !== "COMMENT";
          return (
            <label key={value} className={`verdict ${verdict === value ? "on" : ""} ${blocked ? "blocked" : ""}`} title={blocked ? "GitHub doesn't let you approve or request changes on your own pull request" : undefined}>
              <input type="radio" name="verdict" checked={verdict === value} disabled={blocked} onChange={() => setVerdict(value)} />
              <span>
                <span className="verdict-label">{label}</span>
                <span className="faint small">{hint}</span>
              </span>
            </label>
          );
        })}
      </div>
      {error && <p className="error-text small">{error}</p>}
      <div className="form-actions">
        <span className="faint small">{drafts ? `${drafts} line comment${drafts === 1 ? " goes" : "s go"} with it` : "No line comments"}</span>
        <span className="grow" />
        <button
          className="button small primary"
          disabled={sending || needsBody}
          onClick={async () => {
            setSending(true);
            setError(null);
            try {
              const done = await api.submitReview(pull.repo, pull.number, { event: verdict, body });
              forgetBody(); // sent, and the panel closes
              onDone(done);
            } catch (e) {
              setError(String(e).replace(/^Error: /, ""));
              setSending(false);
            }
          }}
        >
          {sending ? "Submitting…" : "Submit review"}
        </button>
      </div>
    </div>
  );
}
