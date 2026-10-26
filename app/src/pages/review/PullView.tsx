import { useDeferredValue, useEffect, useRef, useState } from "react";
import { api, bridge } from "../../api";
import { Icon } from "../../components/Icon";
import { modelLabel } from "../../components/ModelPicker";
import { MAX_HITS, PullDiff } from "../../components/PullDiff";
import { Markdown, ago } from "../../components/bits";
import { Segmented } from "../../components/ui";
import type { Crew } from "../../state";
import type { Draft, Pull, Submitted, Task } from "../../types";
import { ReviewPane } from "./ReviewPane";
import { SubmitPanel } from "./SubmitPanel";
import { type Selected, people } from "./shared";
// Kept while the app is open, so going back to a pull request shows it at once while it refreshes.
const keptPulls = new Map<string, Pull>();
const keptDiffs = new Map<string, string>();

export function PullView({ crew, selected, onSettings }: { crew: Crew; selected: Selected; onSettings: () => void }) {
  const key = `${selected.repo}#${selected.number}`;
  const [pull, setPull] = useState<Pull | null>(keptPulls.get(key) ?? null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"summary" | "changes">("summary");
  const [diff, setDiff] = useState<string | null>(keptDiffs.get(key) ?? null);
  // Line comments for your review: yours, and the ones the reviewers draft as they go.
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<Submitted | null>(null);
  // Finding in the diff, with ⌘F: the webview has no find of its own.
  const [finding, setFinding] = useState(false);
  const [query, setQuery] = useState("");
  const [current, setCurrent] = useState(0);
  const [found, setFound] = useState(0);
  const findInput = useRef<HTMLInputElement>(null);
  const lookFor = useDeferredValue(finding ? query : ""); // a big diff marks its matches behind your typing
  const step = (by: number) => found && setCurrent((c) => (c + by + found) % found);
  useEffect(() => {
    const keys = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey) return;
      if (e.key.toLowerCase() === "f") {
        e.preventDefault();
        setTab("changes");
        setFinding(true);
        setTimeout(() => findInput.current?.select(), 0);
      } else if (e.key.toLowerCase() === "g" && finding) {
        e.preventDefault();
        step(e.shiftKey ? -1 : 1);
      }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [finding, found]);
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
        {finding && tab === "changes" && (
          <div className="find-bar" role="search">
            <Icon name="search" size={13} />
            <input
              ref={findInput}
              autoFocus
              value={query}
              placeholder="Find in the changes"
              aria-label="Find in the changes"
              onChange={(e) => {
                setQuery(e.target.value);
                setCurrent(0);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") step(e.shiftKey ? -1 : 1);
                if (e.key === "Escape") setFinding(false);
              }}
            />
            <span className="find-count faint">{query ? (found ? `${current + 1} of ${found}${found >= MAX_HITS ? "+" : ""}` : "No matches") : ""}</span>
            <button className="icon-button" onClick={() => step(-1)} disabled={!found} title="Previous (⇧↩)">
              <Icon name="chevronDown" size={13} className="flip" />
            </button>
            <button className="icon-button" onClick={() => step(1)} disabled={!found} title="Next (↩)">
              <Icon name="chevronDown" size={13} />
            </button>
            <button className="icon-button" onClick={() => setFinding(false)} title="Close (esc)">
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
              people={people(pull)}
              find={lookFor}
              current={current}
              onMatches={(n) => {
                setFound(n);
                setCurrent((c) => (c < n ? c : 0));
              }}
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
