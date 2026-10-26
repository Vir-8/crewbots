import { useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { Composer } from "../../components/Composer";
import { Icon } from "../../components/Icon";
import { modelLabel } from "../../components/ModelPicker";
import { Resizer, useWidth } from "../../components/Resizer";
import { Thread } from "../../components/Thread";
import { ago } from "../../components/bits";
import type { Crew } from "../../state";
import { ACTIVE } from "../../types";
import type { Pull, Reviewer, Task } from "../../types";
import { area } from "./shared";
/** "reviewer:claude:sonnet:high" → the reviewer it was. */
function reviewerOf(task: Task): Reviewer {
  const [, backend, model, effort] = task.bot.split(":");
  return { backend: backend as Reviewer["backend"], model: model === "default" ? null : model, effort: effort === "default" ? null : effort };
}

/** "A", "A and B", "A, B and C". */
function listed(names: string[]): string {
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : (names[0] ?? "");
}

/** One review: each reviewer model reviews the pull request on its own, then the lead (the first) checks and puts
 * their findings together, drafts the line comments, and writes the review you read and talk to. The reviewers' own
 * threads are behind "Reviewers", for when you want to see how they got there. */
export function ReviewPane({ crew, pull, reviews, onStarted, onSettings }: { crew: Crew; pull: Pull; reviews: Task[]; onStarted: () => void; onSettings: () => void }) {
  const [paneWidth, setPaneWidth] = useWidth("review-pane");
  const [reviewers, setReviewers] = useState<Reviewer[]>([]);
  const [note, setNote] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const leads = useMemo(() => reviews.filter((r) => !r.parent_id).sort((a, b) => a.created_at.localeCompare(b.created_at)), [reviews]);
  const [round, setRound] = useState<number | null>(null);
  const lead = leads[round ?? leads.length - 1];
  const team = lead ? (crew.children.get(lead.id) ?? reviews.filter((r) => r.parent_id === lead.id)) : [];
  const [showTeam, setShowTeam] = useState(false);
  const [peek, setPeek] = useState<string | null>(null);
  const shown = team.find((t) => t.id === peek) ?? lead;
  const label = (t: Task) => {
    const r = reviewerOf(t);
    return modelLabel(crew, r.backend, r.model ?? null, r.effort ?? null);
  };
  const reviewing = team.filter((t) => ACTIVE.includes(t.status));
  const running = Boolean(lead && (ACTIVE.includes(lead.status) || reviewing.length));
  const working = !lead ? "" : reviewing.length ? `${listed(reviewing.map(label))} ${reviewing.length > 1 ? "are" : "is"} reviewing it` : team.length ? "Putting the reviews together" : "Getting the code";
  useEffect(() => {
    api.reviewers().then(setReviewers, () => setReviewers([]));
    crew.loadModels();
  }, []);
  useEffect(() => setPeek(null), [lead?.id]);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      await api.startReview(pull.repo, pull.number, note);
      setNote("");
      setRound(null);
      onStarted();
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    } finally {
      setStarting(false);
    }
  };
  const names = reviewers.map((r) => modelLabel(crew, r.backend, r.model ?? null, r.effort ?? null));

  return (
    <aside className="review-pane">
      <Resizer label="Resize the review" edge="left" width={paneWidth} min={280} max={760} keep={320} cssVar="--review-pane-user" host={area} pane={() => area()?.querySelector<HTMLElement>(":scope > .review-pane") ?? null} flexible={() => area()?.querySelector<HTMLElement>(":scope > .pull-view") ?? null} onChange={setPaneWidth} />
      <header className="review-pane-head" data-tauri-drag-region="deep">
        <span className="review-pane-title">Private review</span>
        <span className="grow" />
        <button className="icon-button" onClick={onSettings} title="Choose the reviewer models">
          <Icon name="settings" size={14} />
        </button>
        <button className="button small primary" onClick={start} disabled={starting || running}>
          {starting ? "Starting…" : running ? "Reviewing…" : lead ? "Review again" : "Review"}
        </button>
      </header>
      {error && <p className="error-text pad">{error}</p>}
      {lead && shown ? (
        <>
          <div className="review-team">
            <button className={`review-team-toggle ${showTeam ? "on" : ""}`} onClick={() => (setShowTeam(!showTeam), setPeek(null))} disabled={!team.length} title={team.length ? "The reviewers' own threads" : "The reviewers start once the code is ready"} aria-expanded={showTeam}>
              {team.map((t) => (
                <span key={t.id} className={`status-dot ${t.status}`} />
              ))}
              <span>{team.length ? `Reviewers · ${listed(team.map(label))}` : "Reviewers"}</span>
              {team.length > 0 && <Icon name="chevronDown" size={12} className={showTeam ? "flip" : ""} />}
            </button>
            {leads.length > 1 && (
              <select className="round-select" value={round ?? leads.length - 1} onChange={(e) => setRound(Number(e.target.value))} title="Earlier reviews">
                {leads.map((r, i) => (
                  <option key={r.id} value={i}>
                    {i === leads.length - 1 ? "Latest" : ago(r.created_at)}
                  </option>
                ))}
              </select>
            )}
          </div>
          {showTeam && team.length > 0 && (
            <div className="reviewer-tabs">
              <button className={`reviewer-tab ${shown.id === lead.id ? "on" : ""}`} onClick={() => setPeek(null)}>
                The review
              </button>
              {team.map((t) => (
                <button key={t.id} className={`reviewer-tab ${t.id === shown.id ? "on" : ""}`} onClick={() => setPeek(t.id)}>
                  <span className={`status-dot ${t.status}`} />
                  {label(t)}
                </button>
              ))}
            </div>
          )}
          <div className="review-thread">
            <Thread crew={crew} task={shown} onOpen={() => undefined} compact working={shown.id === lead.id ? working : undefined} />
          </div>
          {shown.id === lead.id ? (
            <div className="review-composer">
              <Composer key={lead.id} placeholder="Ask about this review" onSend={(text, files) => crew.send(lead.id, text, files)} task={lead.id} draft={`task:${lead.id}`} />
            </div>
          ) : (
            <p className="review-hint review-peek faint">
              {label(shown)}'s own review, before the lead checked it against the code and put it together with the others'. Ask about it in <a className="task-link" onClick={() => setPeek(null)}>the review</a>.
            </p>
          )}
        </>
      ) : (
        <div className="review-start">
          <Icon name="branch" size={22} />
          <p>
            {names.length > 1
              ? `${listed(names)} each review it on their own; then ${names[0]} checks their findings, puts them into one review and drafts its line comments for you.`
              : names.length
                ? `${names[0]} reviews it and drafts its line comments for you.`
                : "Reviewers read it and draft line comments for you."}{" "}
            They read the code; nothing is run, and nothing is posted to GitHub.
          </p>
          <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything to look at in particular? (optional)" />
        </div>
      )}
    </aside>
  );
}
