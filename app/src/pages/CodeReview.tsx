import { useEffect, useMemo, useState } from "react";
import { api, bridge } from "../api";
import { Markdown, ago } from "../components/bits";
import { Composer } from "../components/Composer";
import { PullDiff } from "../components/PullDiff";
import { Icon } from "../components/Icon";
import { modelLabel } from "../components/ModelPicker";
import { Resizer, useWidth } from "../components/Resizer";
import { Thread } from "../components/Thread";
import { Segmented } from "../components/ui";
import type { Crew } from "../state";
import type { Draft, Pull, PullSummary, Reviewer, Submitted, Task } from "../types";
import { ACTIVE } from "../types";

type Selected = { repo: string; number: number };

const area = () => document.querySelector<HTMLElement>(".review-area"); // the grid both edges resize

// Kept while the app is open, so going back to a pull request or the teams' list shows it at once while it refreshes.
let keptTeam: PullSummary[] | null = null;
const keptPulls = new Map<string, Pull>();
const keptDiffs = new Map<string, string>();

/** Pull requests waiting on you, read through your gh login, and private reviews of them. The reviewer isn't one of
 * your bots: it's its own agent, run on each model you choose, side by side. Nothing here posts to GitHub. */
export function CodeReview({ crew, onSettings }: { crew: Crew; onSettings: () => void }) {
  const [selected, setSelected] = useState<Selected | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const inbox = crew.reviewInbox;
  // The kept lists show at once; opening code review asks GitHub for new ones behind them.
  useEffect(() => {
    setRefreshing(true);
    crew.loadReviews(true).finally(() => setRefreshing(false));
  }, []);
  const first = inbox?.assigned?.[0] ?? inbox?.direct?.[0];
  // Your teams' pull requests are often the longest list and the slowest to ask for, so only when you open them.
  const [team, setTeam] = useState<PullSummary[] | null>(keptTeam);
  const [teamError, setTeamError] = useState<string | null>(null);
  const loadTeam = async (fresh = false) => {
    try {
      const answer = await api.reviewInbox(fresh, ["team"]);
      keptTeam = answer.team ?? [];
      setTeam(keptTeam);
      setTeamError(answer.error);
    } catch (e) {
      setTeamError(String(e).replace(/^Error: /, ""));
    }
  };
  useEffect(() => {
    if (!selected && first) setSelected({ repo: first.repo, number: first.number });
  }, [first?.repo, first?.number]);

  const [listWidth, setListWidth] = useWidth("review-list");
  return (
    <div className="review-area">
      <aside className="review-list">
        <Resizer label="Resize the list" edge="right" width={listWidth} min={200} max={480} keep={320} cssVar="--review-list-user" host={area} pane={() => area()?.querySelector<HTMLElement>(":scope > .review-list") ?? null} flexible={() => area()?.querySelector<HTMLElement>(":scope > .pull-view, :scope > .review-empty") ?? null} onChange={setListWidth} />
        <header className="review-list-head" data-tauri-drag-region="deep">
          <h1>Code review</h1>
          <span className="grow" />
          <button
            className="icon-button"
            title="Refresh"
            onClick={async () => {
              setRefreshing(true);
              await Promise.all([crew.loadReviews(true), team && loadTeam(true)]);
              setRefreshing(false);
            }}
          >
            <Icon name="repeat" size={14} className={refreshing ? "spin" : ""} />
          </button>
        </header>
        <div className="review-list-scroll">
          {inbox?.error && <p className="error-text pad">{inbox.error}</p>}
          {!inbox && <p className="faint pad">Asking GitHub…</p>}
          <PullGroup title="Assigned to you" pulls={inbox?.assigned ?? []} selected={selected} onSelect={setSelected} empty="Nothing is assigned to you." />
          <PullGroup title="Asks for your review" pulls={(inbox?.direct ?? []).filter((p) => !inbox?.assigned?.some((a) => a.repo === p.repo && a.number === p.number))} selected={selected} onSelect={setSelected} />
          <PullGroup title="Authored by you" pulls={inbox?.mine ?? []} selected={selected} onSelect={setSelected} />
          <PullGroup
            title="Your teams"
            pulls={team}
            selected={selected}
            onSelect={setSelected}
            collapsed
            onOpen={() => loadTeam()}
            empty={teamError ?? "Nothing asks your teams for a review."}
          />
        </div>
      </aside>
      {selected ? (
        <PullView key={`${selected.repo}#${selected.number}`} crew={crew} selected={selected} onSettings={onSettings} />
      ) : (
        <div className="review-empty faint">Choose a pull request.</div>
      )}
    </div>
  );
}

/** One list of pull requests. `pulls` is null until it's been asked for: `onOpen` asks, the first time it's opened. */
function PullGroup(props: {
  title: string;
  pulls: PullSummary[] | null;
  selected: Selected | null;
  onSelect: (s: Selected) => void;
  empty?: string;
  collapsed?: boolean;
  onOpen?: () => void;
}) {
  const [open, setOpen] = useState(!props.collapsed);
  const pulls = props.pulls ?? [];
  if (props.pulls && pulls.length === 0 && !props.empty) return null;
  return (
    <section className="pull-group">
      <button
        className="pull-group-head"
        onClick={() => {
          if (!open) props.onOpen?.();
          setOpen(!open);
        }}
      >
        <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
        {props.title}
        {props.pulls && <span className="faint">{pulls.length}</span>}
      </button>
      {open && !props.pulls && <p className="faint small pad-x">Asking GitHub…</p>}
      {open && props.pulls && pulls.length === 0 && <p className="faint small pad-x">{props.empty}</p>}
      {open &&
        pulls.map((p) => {
          const on = props.selected?.repo === p.repo && props.selected?.number === p.number;
          return (
            <button key={`${p.repo}#${p.number}`} className={`pull-row ${on ? "on" : ""}`} onClick={() => props.onSelect({ repo: p.repo, number: p.number })}>
              <span className="pull-row-title">
                {p.draft && <span className="draft-tag">Draft</span>}
                {p.title}
              </span>
              <span className="pull-row-meta">
                {p.author_avatar ? <img className="gh-avatar" src={p.author_avatar} alt="" /> : <span className="gh-avatar" />}
                <span className="faint">
                  {p.author} · {p.repo.split("/")[1]}#{p.number} · {ago(p.updated_at ?? p.created_at ?? "", true)}
                </span>
                <span className="grow" />
                {p.reviewed_at && <span className="reviewed-dot" title="You have a review of this" />}
                {(p.checks === "FAILURE" || p.checks === "ERROR") && <Icon name="x" size={13} className="check-failed" />}
              </span>
            </button>
          );
        })}
    </section>
  );
}

function PullView({ crew, selected, onSettings }: { crew: Crew; selected: Selected; onSettings: () => void }) {
  const key = `${selected.repo}#${selected.number}`;
  const [pull, setPull] = useState<Pull | null>(keptPulls.get(key) ?? null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"summary" | "changes">("summary");
  const [diff, setDiff] = useState<string | null>(keptDiffs.get(key) ?? null);
  // Line comments for your review: yours, and the ones the reviewers draft as they go.
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<Submitted | null>(null);
  const loadDrafts = () => api.drafts(selected.repo, selected.number).then(setDrafts, () => undefined);
  // A kept one shows at once; the daemon's answer, kept there too, replaces it a moment later.
  const load = () =>
    api.pull(selected.repo, selected.number).then(
      (p) => {
        keptPulls.set(key, p);
        setPull(p);
      },
      (e) => keptPulls.has(key) || setError(String(e).replace(/^Error: /, "")),
    );
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    if (tab !== "changes") return;
    api.pullDiff(selected.repo, selected.number).then(
      (d) => {
        keptDiffs.set(key, d.diff ?? "");
        setDiff(d.diff ?? "");
      },
      (e) => keptDiffs.has(key) || setDiff(`Couldn't load the diff: ${e}`),
    );
  }, [tab]);

  const reviewTasks = (pull?.reviews ?? []).map((r) => crew.tasks.get(r.id) ?? (r as Task));
  useEffect(() => {
    loadDrafts();
  }, [reviewTasks.map((r) => r.updated_at).join()]);
  const authorOf = (who: string) => {
    if (who === "user") return "You";
    const [, backend, model, effort] = who.split(":");
    return modelLabel(crew, backend ?? "claude", model === "default" ? null : model, effort === "default" ? null : effort);
  };
  const goTo = (id: number) => {
    setTab("changes");
    setTimeout(() => document.getElementById(`draft-${id}`)?.scrollIntoView({ block: "center", behavior: "smooth" }), 400);
  };

  if (error) return <div className="review-empty error-text">{error}</div>;
  if (!pull) return <div className="review-empty faint">Loading {selected.repo}#{selected.number}…</div>;
  const reviews = reviewTasks;
  const yours = Boolean(crew.reviewInbox?.mine?.some((p) => p.repo === pull.repo && p.number === pull.number));
  return (
    <>
      <main className="pull-view">
        <header className="pull-head" data-tauri-drag-region="deep">
          <Segmented value={tab} options={[["summary", "Summary"], ["changes", "Changes"]]} onChange={setTab} />
          <span className="pull-delta">
            <span className="add">+{pull.additions}</span> <span className="del">−{pull.deletions}</span>
          </span>
          <span className="grow" />
          <span className="submit-anchor">
            <button className={`button small submit-button ${drafts.length ? "primary" : ""}`} onClick={() => setSubmitting(!submitting)} title="Approve, request changes or comment, on GitHub" aria-haspopup="dialog" aria-expanded={submitting}>
              Submit<span className="long-label"> review</span>
              {drafts.length ? ` (${drafts.length})` : ""}
              <Icon name="chevronDown" size={12} className={submitting ? "flip" : ""} />
            </button>
            {submitting && (
              <SubmitPanel
                pull={pull}
                drafts={drafts.length}
                yours={yours}
                onClose={() => setSubmitting(false)}
                onDone={(done) => {
                  setSubmitted(done);
                  setSubmitting(false);
                  loadDrafts();
                  load();
                }}
              />
            )}
          </span>
          <button className="icon-button github-link" onClick={() => bridge.openUrl(pull.url)} title="Open on GitHub">
            <Icon name="external" size={14} />
          </button>
        </header>
        {submitted && (
          <div className="submitted">
            <Icon name="check" size={14} />
            <span>
              {submitted.state === "APPROVED" ? "Approved" : submitted.state === "CHANGES_REQUESTED" ? "Requested changes" : "Commented"} on GitHub
              {submitted.comments ? `, with ${submitted.comments} line comment${submitted.comments === 1 ? "" : "s"}` : ""}.
            </span>
            {submitted.url && (
              <button className="text-button" onClick={() => bridge.openUrl(submitted.url)}>
                See it
              </button>
            )}
            <span className="grow" />
            <button className="icon-button" onClick={() => setSubmitted(null)} title="Dismiss">
              <Icon name="x" size={13} />
            </button>
          </div>
        )}
        <div className="pull-scroll">
          {tab === "summary" ? (
            <div className="pull-col">
              <div className="pull-state">
                <span className={`state-pill ${pull.draft ? "draft" : "open"}`}>{pull.draft ? "Draft" : "Open"}</span>
                <button className="text-button pull-ref" onClick={() => bridge.openUrl(pull.url)} title="Open on GitHub">
                  {pull.repo}#{pull.number}
                  <Icon name="external" size={12} />
                </button>
              </div>
              <h1 className="pull-title">{pull.title}</h1>
              <div className="faint pull-by">
                {pull.author} · {ago(pull.created_at ?? "")} · <span className="mono">{pull.head}</span> → <span className="mono">{pull.base}</span>
              </div>
              <div className="pull-facts">
                <div>
                  <Icon name="diff" size={14} />
                  {pull.changed_files} files <span className="add">+{pull.additions}</span> <span className="del">−{pull.deletions}</span>
                </div>
                <div className={pull.checks.failed ? "bad" : ""} title={pull.failing.join("\n")}>
                  <Icon name={pull.checks.failed ? "x" : "check"} size={14} />
                  {pull.checks.failed ? `${pull.checks.failed} failing of ${pull.checks.total} checks: ${pull.failing.slice(0, 2).join(", ")}${pull.failing.length > 2 ? "…" : ""}` : `${pull.checks.passed} of ${pull.checks.total} checks passed`}
                </div>
                {pull.review_decision && (
                  <div>
                    <Icon name="question" size={14} />
                    {pull.review_decision.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase())}
                  </div>
                )}
              </div>
              {drafts.length > 0 && (
                <div className="pending-comments">
                  <h3>Your review's line comments · {drafts.length}, not submitted</h3>
                  {drafts.map((d) => (
                    <button key={d.id} className="pending-comment" onClick={() => goTo(d.id)}>
                      <span className="mono small">
                        {d.path}:{d.line}
                      </span>
                      <span className="faint small">{authorOf(d.author)}</span>
                      <span className="pending-text">{d.body.split("\n")[0]}</span>
                    </button>
                  ))}
                </div>
              )}
              <div className="pull-body">{pull.body.trim() ? <Markdown text={pull.body} onSelect={() => undefined} /> : <p className="faint">No description.</p>}</div>
            </div>
          ) : diff === null ? (
            <p className="faint pad">Loading the diff…</p>
          ) : (
            <PullDiff
              diff={diff}
              drafts={drafts}
              author={authorOf}
              onAdd={async (spot, body) => {
                await api.addDraft(pull.repo, pull.number, { ...spot, body });
                await loadDrafts();
              }}
              onEdit={async (id, body) => {
                await api.editDraft(id, body);
                await loadDrafts();
              }}
              onDelete={async (id) => {
                await api.deleteDraft(id);
                await loadDrafts();
              }}
              image={(path, side) => api.pullImage(pull.repo, pull.number, path, side).then((i) => `data:${i.mime};base64,${i.data}`)}
            />
          )}
        </div>
      </main>
      <ReviewPane crew={crew} pull={pull} reviews={reviews} onStarted={load} onSettings={onSettings} />
    </>
  );
}

type Verdict = "COMMENT" | "APPROVE" | "REQUEST_CHANGES";
const VERDICTS: [Verdict, string, string][] = [
  ["COMMENT", "Comment", "Feedback, without approving or holding it up"],
  ["APPROVE", "Approve", "Good to merge"],
  ["REQUEST_CHANGES", "Request changes", "Must be addressed before it's merged"],
];

/** Your review, posted to GitHub under your name: the only thing crewbots ever submits, and only from here. */
function SubmitPanel({ pull, drafts, yours, onClose, onDone }: { pull: Pull; drafts: number; yours: boolean; onClose: () => void; onDone: (done: Submitted) => void }) {
  const [verdict, setVerdict] = useState<Verdict>("COMMENT");
  const [body, setBody] = useState("");
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
      <textarea rows={4} autoFocus value={body} onChange={(e) => setBody(e.target.value)} placeholder="Leave a summary (optional when approving)" />
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
        <span className="faint small">{drafts ? `${drafts} line comment${drafts === 1 ? "" : "s"} go with it` : "No line comments"}</span>
        <span className="grow" />
        <button
          className="button small primary"
          disabled={sending || needsBody}
          onClick={async () => {
            setSending(true);
            setError(null);
            try {
              onDone(await api.submitReview(pull.repo, pull.number, { event: verdict, body }));
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

/** "reviewer:claude:sonnet:high" → the reviewer it was. */
function reviewerOf(task: Task): Reviewer {
  const [, backend, model, effort] = task.bot.split(":");
  return { backend: backend as Reviewer["backend"], model: model === "default" ? null : model, effort: effort === "default" ? null : effort };
}

/** Reviews run together are one round; the newest round shows, a tab per model. */
function rounds(reviews: Task[]): Task[][] {
  const sorted = [...reviews].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const out: Task[][] = [];
  for (const r of sorted) {
    const last = out.at(-1);
    if (last && Date.parse(r.created_at) - Date.parse(last[0].created_at) < 30_000) last.push(r);
    else out.push([r]);
  }
  return out;
}

function ReviewPane({ crew, pull, reviews, onStarted, onSettings }: { crew: Crew; pull: Pull; reviews: Task[]; onStarted: () => void; onSettings: () => void }) {
  const [paneWidth, setPaneWidth] = useWidth("review-pane");
  const [reviewers, setReviewers] = useState<Reviewer[]>([]);
  const [note, setNote] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const all = useMemo(() => rounds(reviews), [reviews]);
  const [round, setRound] = useState<number | null>(null);
  const current = all[round ?? all.length - 1] ?? [];
  const [tab, setTab] = useState(0);
  const shown = current[Math.min(tab, current.length - 1)];
  const running = current.some((t) => ACTIVE.includes(t.status));
  useEffect(() => {
    api.reviewers().then(setReviewers, () => setReviewers([]));
    crew.loadModels();
  }, []);
  useEffect(() => setTab(0), [all.length]);

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
          {starting ? "Starting…" : running ? "Reviewing…" : current.length ? "Review again" : "Review"}
        </button>
      </header>
      {!pull.checkout && (
        <p className="review-hint faint">
          Reviewers get the changed files from GitHub and look up the rest there as they need it. <a className="task-link" onClick={onSettings}>Point {pull.repo} at your copy</a> to give them the whole repository.
        </p>
      )}
      {error && <p className="error-text pad">{error}</p>}
      {shown ? (
        <>
          <div className="reviewer-tabs">
            {current.map((t, i) => {
              const r = reviewerOf(t);
              return (
                <button key={t.id} className={`reviewer-tab ${t.id === shown.id ? "on" : ""}`} onClick={() => setTab(i)}>
                  <span className={`status-dot ${t.status}`} />
                  {modelLabel(crew, r.backend, r.model ?? null, r.effort ?? null)}
                </button>
              );
            })}
            {all.length > 1 && (
              <select className="round-select" value={round ?? all.length - 1} onChange={(e) => setRound(Number(e.target.value))} title="Earlier reviews">
                {all.map((r, i) => (
                  <option key={r[0].id} value={i}>
                    {i === all.length - 1 ? "Latest" : ago(r[0].created_at)}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="review-thread">
            <Thread crew={crew} task={shown} onOpen={() => undefined} compact />
          </div>
          <div className="review-composer">
            <Composer key={shown.id} placeholder="Ask about this review" onSend={(text, files) => crew.send(shown.id, text, files)} task={shown.id} />
          </div>
        </>
      ) : (
        <div className="review-start">
          <Icon name="branch" size={22} />
          <p>
            {names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)} review it side by side` : names.length ? `${names[0]} reviews it` : "Reviewers read it"}, looking for actionable bugs and the overall impact. Nothing is posted to GitHub.
          </p>
          <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything to look at in particular? (optional)" />
        </div>
      )}
    </aside>
  );
}
