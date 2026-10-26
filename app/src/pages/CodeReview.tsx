import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import { Avatar, labelOf } from "../components/Avatar";
import { Markdown, ago } from "../components/bits";
import { Composer } from "../components/Composer";
import { DiffText } from "../components/DiffView";
import { Icon } from "../components/Icon";
import { Thread } from "../components/Thread";
import { Segmented } from "../components/ui";
import type { Crew } from "../state";
import type { Pull, PullSummary } from "../types";
import { ACTIVE } from "../types";

type Selected = { repo: string; number: number };

/** Pull requests waiting on you, read through your gh login, and private reviews of them by a bot. Nothing here posts
 * to GitHub: a review is a conversation between you and the bot. */
export function CodeReview({ crew, onSettings }: { crew: Crew; onSettings: () => void }) {
  const [selected, setSelected] = useState<Selected | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const inbox = crew.reviewInbox;
  const first = inbox?.direct[0] ?? inbox?.mine[0] ?? inbox?.team[0];
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
          <PullGroup title="Authored by you" pulls={inbox?.mine ?? []} selected={selected} onSelect={setSelected} />
          <PullGroup title="Your teams" pulls={inbox?.team ?? []} selected={selected} onSelect={setSelected} collapsed />
        </div>
      </aside>
      {selected ? <PullView key={`${selected.repo}#${selected.number}`} crew={crew} selected={selected} onSettings={onSettings} /> : <div className="review-empty faint">Choose a pull request.</div>}
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
  const reviewIds = pull?.reviews.map((r) => r.id) ?? [];
  const reviews = reviewIds.map((id) => crew.tasks.get(id)).filter((t) => t !== undefined);
  const load = () => api.pull(selected.repo, selected.number).then(setPull, (e) => setError(String(e).replace(/^Error: /, "")));
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    if (tab === "changes" && diff === null) api.pullDiff(selected.repo, selected.number).then((d) => setDiff(d.diff ?? ""), (e) => setDiff(`Couldn't load the diff: ${e}`));
  }, [tab]);

  if (error) return <div className="review-empty error-text">{error}</div>;
  if (!pull) return <div className="review-empty faint">Loading {selected.repo}#{selected.number}…</div>;
  return (
    <>
      <main className="pull-view">
        <header className="pull-head" data-tauri-drag-region>
          <Segmented value={tab} options={[["summary", "Summary"], ["changes", `Changes +${pull.additions} −${pull.deletions}`]]} onChange={setTab} />
          <span className="grow" data-tauri-drag-region />
          <button className="button small" onClick={() => bridge.openUrl(pull.url)} title="Open on GitHub">
            GitHub
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
                <div className={pull.checks.failed ? "bad" : ""}>
                  <Icon name={pull.checks.failed ? "x" : "check"} size={14} />
                  {pull.checks.failed ? `${pull.checks.failed} failing of ${pull.checks.total} checks` : `${pull.checks.passed} of ${pull.checks.total} checks passed`}
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

function ReviewPane({ crew, pull, reviews, onStarted, onSettings }: { crew: Crew; pull: Pull; reviews: NonNullable<ReturnType<Crew["tasks"]["get"]>>[]; onStarted: () => void; onSettings: () => void }) {
  const reviewers = crew.bots.filter((b) => b.access === "read_only");
  const [bot, setBot] = useState(reviewers.find((b) => b.name === "reviewer")?.name ?? reviewers[0]?.name ?? crew.bots[0]?.name ?? "");
  const [note, setNote] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latest = reviews.at(-1);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      await api.startReview(pull.repo, pull.number, bot, note);
      setNote("");
      onStarted();
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    } finally {
      setStarting(false);
    }
  };

  return (
    <aside className="review-pane">
      <header className="review-pane-head">
        <span className="review-pane-title">Private review</span>
        <span className="grow" />
        <select value={bot} onChange={(e) => setBot(e.target.value)} title="Who reviews">
          {crew.bots.map((b) => (
            <option key={b.name} value={b.name}>
              {labelOf(b.name)}
            </option>
          ))}
        </select>
        <button className="button small primary" onClick={start} disabled={starting || (latest !== undefined && ACTIVE.includes(latest.status))}>
          {starting ? "Starting…" : latest ? "Review again" : "Review"}
        </button>
      </header>
      {!pull.checkout && (
        <p className="review-hint faint">
          Reviews read the diff only. <a className="task-link" onClick={onSettings}>Point {pull.repo} at your copy</a> to give them the code.
        </p>
      )}
      {error && <p className="error-text pad">{error}</p>}
      {latest ? (
        <>
          <div className="review-thread">
            <Thread crew={crew} task={latest} onOpen={() => undefined} compact />
          </div>
          <div className="review-composer">
            <Composer key={latest.id} placeholder={`Ask ${labelOf(latest.bot)} about this pull request`} onSend={(text) => crew.send(latest.id, text)} />
          </div>
        </>
      ) : (
        <div className="review-start">
          <Avatar name={bot} size={44} />
          <p>
            {labelOf(bot)} reads the pull request and comes back with actionable bugs and the overall impact. Nothing is posted to GitHub.
          </p>
          <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Anything to look at in particular? (optional)" />
        </div>
      )}
    </aside>
  );
}
