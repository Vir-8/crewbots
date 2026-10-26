import { useEffect, useMemo, useState } from "react";
import { api, bridge } from "../api";
import { Markdown, ago } from "../components/bits";
import { Composer } from "../components/Composer";
import { DiffText } from "../components/DiffView";
import { Icon } from "../components/Icon";
import { modelLabel } from "../components/ModelPicker";
import { Thread } from "../components/Thread";
import { Segmented } from "../components/ui";
import type { Crew } from "../state";
import type { Pull, PullSummary, Reviewer, Task } from "../types";
import { ACTIVE } from "../types";

type Selected = { repo: string; number: number };

/** Pull requests waiting on you, read through your gh login, and private reviews of them. The reviewer isn't one of
 * your bots: it's its own agent, run on each model you choose, side by side. Nothing here posts to GitHub. */
export function CodeReview({ crew, onSettings }: { crew: Crew; onSettings: () => void }) {
  const [selected, setSelected] = useState<Selected | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const inbox = crew.reviewInbox;
  const first = inbox?.direct[0] ?? inbox?.assigned[0] ?? inbox?.mine[0] ?? inbox?.team[0];
  useEffect(() => {
    if (!selected && first) setSelected({ repo: first.repo, number: first.number });
  }, [first?.repo, first?.number]);

  return (
    <div className="review-area">
      <aside className="review-list">
        <header className="review-list-head" data-tauri-drag-region>
          <h1 data-tauri-drag-region>Code review</h1>
          <span className="grow" data-tauri-drag-region />
          <button
            className="icon-button"
            title="Refresh"
            onClick={async () => {
              setRefreshing(true);
              await crew.loadReviews(true);
              setRefreshing(false);
            }}
          >
            <Icon name="repeat" size={14} className={refreshing ? "spin" : ""} />
          </button>
        </header>
        <div className="review-list-scroll">
          {inbox?.error && <p className="error-text pad">{inbox.error}</p>}
          {!inbox && <p className="faint pad">Asking GitHub…</p>}
          <PullGroup title="Needs your review" pulls={inbox?.direct ?? []} selected={selected} onSelect={setSelected} empty="Nothing is waiting on you." />
          <PullGroup title="Assigned to you" pulls={inbox?.assigned ?? []} selected={selected} onSelect={setSelected} />
          <PullGroup title="Authored by you" pulls={inbox?.mine ?? []} selected={selected} onSelect={setSelected} />
          <PullGroup title="Your teams" pulls={inbox?.team ?? []} selected={selected} onSelect={setSelected} collapsed />
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

function PullGroup(props: { title: string; pulls: PullSummary[]; selected: Selected | null; onSelect: (s: Selected) => void; empty?: string; collapsed?: boolean }) {
  const [open, setOpen] = useState(!props.collapsed);
  if (props.pulls.length === 0 && !props.empty) return null;
  return (
    <section className="pull-group">
      <button className="pull-group-head" onClick={() => setOpen(!open)}>
        <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
        {props.title}
        <span className="faint">{props.pulls.length}</span>
      </button>
      {open && props.pulls.length === 0 && <p className="faint small pad-x">{props.empty}</p>}
      {open &&
        props.pulls.map((p) => {
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
  const [pull, setPull] = useState<Pull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"summary" | "changes">("summary");
  const [diff, setDiff] = useState<string | null>(null);
  const load = () => api.pull(selected.repo, selected.number).then(setPull, (e) => setError(String(e).replace(/^Error: /, "")));
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    if (tab === "changes" && diff === null) api.pullDiff(selected.repo, selected.number).then((d) => setDiff(d.diff ?? ""), (e) => setDiff(`Couldn't load the diff: ${e}`));
  }, [tab]);

  if (error) return <div className="review-empty error-text">{error}</div>;
  if (!pull) return <div className="review-empty faint">Loading {selected.repo}#{selected.number}…</div>;
  const reviews = pull.reviews.map((r) => crew.tasks.get(r.id) ?? (r as Task));
  return (
    <>
      <main className="pull-view">
        <header className="pull-head" data-tauri-drag-region>
          <Segmented value={tab} options={[["summary", "Summary"], ["changes", "Changes"]]} onChange={setTab} />
          <span className="pull-delta">
            <span className="add">+{pull.additions}</span> <span className="del">−{pull.deletions}</span>
          </span>
          <span className="grow" data-tauri-drag-region />
          <button className="icon-button" onClick={() => bridge.openUrl(pull.url)} title="Open on GitHub">
            <Icon name="external" size={14} />
          </button>
        </header>
        <div className="pull-scroll">
          {tab === "summary" ? (
            <div className="pull-col">
              <div className="pull-state">
                <span className={`state-pill ${pull.draft ? "draft" : "open"}`}>{pull.draft ? "Draft" : "Open"}</span>
                <span className="faint">
                  {pull.repo}#{pull.number}
                </span>
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
              <div className="pull-body">{pull.body.trim() ? <Markdown text={pull.body} onSelect={() => undefined} /> : <p className="faint">No description.</p>}</div>
            </div>
          ) : diff === null ? (
            <p className="faint pad">Loading the diff…</p>
          ) : (
            <DiffText diff={diff} />
          )}
        </div>
      </main>
      <ReviewPane crew={crew} pull={pull} reviews={reviews} onStarted={load} onSettings={onSettings} />
    </>
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
      <header className="review-pane-head">
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
          Reviewers read the diff only. <a className="task-link" onClick={onSettings}>Point {pull.repo} at your copy</a> to give them the code.
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
            <Composer key={shown.id} placeholder="Ask about this review" onSend={(text) => crew.send(shown.id, text)} />
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
