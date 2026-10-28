import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import { Icon } from "../components/Icon";
import { ModelPicker } from "../components/ModelPicker";
import { Page, Segmented, Switch } from "../components/ui";
import type { Crew } from "../state";
import type { Reviewer } from "../types";

/** How crewbots itself is set up: starting at login, where the crew lives, GitHub, and code review's local copies. */
export function Settings({ crew }: { crew: Crew }) {
  const [login, setLogin] = useState<boolean | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [autoMerge, setAutoMerge] = useState<boolean | null>(null);
  const [mergeError, setMergeError] = useState<string | null>(null);
  const [repos, setRepos] = useState<Record<string, string>>({});
  const [repo, setRepo] = useState("");
  const [repoError, setRepoError] = useState<string | null>(null);
  useEffect(() => {
    bridge.startAtLogin().then(setLogin, () => setLogin(null));
    api.reviewRepos().then(setRepos, () => setRepos({}));
    api.mergeSettings().then((s) => setAutoMerge(s.auto_merge), (e) => setMergeError(String(e)));
  }, []);

  const problems = [
    crew.routines?.crew_error && { what: "A role card couldn't be read, so the crew carries on as it was", why: crew.routines.crew_error },
    ...Object.entries(crew.routines?.errors ?? {}).map(([file, why]) => ({ what: `routines/${file} couldn't be read`, why })),
  ].filter(Boolean) as { what: string; why: string }[];
  const reviewed = new Set([...(crew.reviewInbox?.assigned ?? []), ...(crew.reviewInbox?.direct ?? [])].map((p) => p.repo));
  const suggestions = [...reviewed].filter((r) => !repos[r]);

  const map = async (name: string) => {
    setRepoError(null);
    const folder = await bridge.pickFolder();
    if (!folder) return;
    try {
      setRepos(await api.setReviewRepo(name, folder));
      setRepo("");
    } catch (e) {
      setRepoError(String(e).replace(/^Error: /, ""));
    }
  };

  return (
    <Page title="Settings">
      {problems.length > 0 && (
        <section className="page-section">
          {problems.map((p) => (
            <div key={p.what} className="crew-problem">
              <Icon name="alert" size={14} />
              <div>
                <div>{p.what}</div>
                <div className="crew-problem-why">{p.why}</div>
              </div>
            </div>
          ))}
        </section>
      )}

      <section className="page-section">
        <h2>General</h2>
        <div className="list">
          <div className="list-row">
            <span className="list-text">
              <span className="list-title">Start at login</span>
              <span className="faint">Keeps the crew running whenever you're logged in, with the app closed. Routines need it.</span>
              {loginError && <span className="error-text">{loginError}</span>}
            </span>
            <Switch
              on={Boolean(login)}
              disabled={login === null}
              label="Start at login"
              onChange={async (on) => {
                setLoginError(null);
                try {
                  setLogin(await bridge.startAtLogin(on));
                } catch (e) {
                  setLoginError(String(e));
                }
              }}
            />
          </div>
          <div className="list-row">
            <span className="list-text">
              <span className="list-title">Merge approved work automatically</span>
              <span className="faint">Merge locally after every review passes. You still push and deploy.</span>
              {mergeError && <span className="error-text">{mergeError}</span>}
            </span>
            <Switch on={Boolean(autoMerge)} disabled={autoMerge === null} label="Merge approved work automatically" onChange={async (on) => {
              setMergeError(null);
              try { setAutoMerge((await api.setMergeSettings(on)).auto_merge); }
              catch (e) { setMergeError(String(e)); }
            }} />
          </div>
          <div className="list-row">
            <span className="list-text">
              <span className="list-title">Crew folder</span>
              <span className="faint mono">{crew.status.home}</span>
            </span>
            <button className="button small" onClick={() => bridge.reveal(crew.status.home)}>
              <Icon name="folder" size={13} />
              Show in Finder
            </button>
          </div>
          <div className="list-row">
            <span className="list-text">
              <span className="list-title">Pause the crew</span>
              <span className="faint">No new turns start while paused; ones already running finish.</span>
            </span>
            <Switch on={Boolean(crew.crewState?.paused)} label="Pause the crew" onChange={(on) => crew.pause(on)} />
          </div>
        </div>
      </section>

      <section className="page-section">
        <h2>GitHub</h2>
        <div className="list">
          <div className="list-row">
            <span className="list-text">
              <span className="list-title">{crew.reviewInbox?.error ? "Not connected" : "Connected through gh"}</span>
              <span className={crew.reviewInbox?.error ? "error-text" : "faint"}>
                {crew.reviewInbox?.error ?? "Code review and the GitHub routines read with your own gh login. Nothing is posted."}
              </span>
            </span>
          </div>
        </div>
      </section>

      <Reviewers crew={crew} />

      <section className="page-section">
        <h2>Code review's local copies</h2>
        <p className="faint small">
          Reviewers always get the whole repository at the pull request's head, from crewbots' own copy of it (the first review of a repository fetches it, in about a minute). Point a repo at your copy to check pull requests out beside yours instead.
        </p>
        <div className="list">
          {Object.entries(repos).map(([name, folder]) => (
            <div key={name} className="list-row">
              <span className="list-text">
                <span className="list-title">{name}</span>
                <span className="faint mono">{folder}</span>
              </span>
              <button className="button small" onClick={() => map(name)}>
                Change
              </button>
              <button className="icon-button" title="Remove" onClick={async () => setRepos(await api.setReviewRepo(name, null))}>
                <Icon name="x" size={14} />
              </button>
            </div>
          ))}
          {suggestions.map((name) => (
            <div key={name} className="list-row">
              <span className="list-text">
                <span className="list-title">{name}</span>
                <span className="faint">Has pull requests waiting on you</span>
              </span>
              <button className="button small" onClick={() => map(name)}>
                <Icon name="folder" size={13} />
                Choose its folder
              </button>
            </div>
          ))}
          <div className="list-row">
            <input className="inline-input" value={repo} onChange={(e) => setRepo(e.target.value.trim())} placeholder="owner/name" />
            <button className="button small" disabled={!/^[\w.-]+\/[\w.-]+$/.test(repo)} onClick={() => map(repo)}>
              <Icon name="folder" size={13} />
              Choose its folder
            </button>
          </div>
          {repoError && <p className="error-text">{repoError}</p>}
        </div>
      </section>
    </Page>
  );
}

/** The models that review a pull request, each on its own, the first leading. Two model families catch different things. */
function Reviewers({ crew }: { crew: Crew }) {
  const [reviewers, setReviewers] = useState<Reviewer[] | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.reviewers().then(setReviewers, (e) => setError(String(e)));
  }, []);
  if (!reviewers) return null;

  const change = (i: number, fields: Partial<Reviewer>) => {
    setSaved(null);
    setReviewers(reviewers.map((r, j) => (j === i ? { ...r, ...fields } : r)));
  };
  const save = async () => {
    setError(null);
    try {
      setReviewers(await api.setReviewers(reviewers));
      setSaved("Saved");
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    }
  };
  return (
    <section className="page-section">
      <div className="section-head">
        <h2>Code review: reviewers</h2>
        <span className="grow" />
        {saved && <span className="faint small">{saved}</span>}
        <button className="button small primary" onClick={save}>
          Save
        </button>
      </div>
      <p className="faint small">
        Pressing Review has each of these review the pull request on its own, with the reviewer's own instructions: not your bots. Then the first, as the lead, checks their findings, puts them into one review and drafts its line comments; that review is a conversation you can carry on.
      </p>
      <div className="list">
        {reviewers.map((r, i) => (
          <div key={i} className="list-row reviewer-row">
            <Segmented value={r.backend} options={[["claude", "Claude"], ["codex", "Codex"]]} onChange={(backend) => change(i, { backend, model: null, effort: null })} />
            <ModelPicker crew={crew} backend={r.backend} model={r.model} effort={r.effort} onChange={(model, effort) => change(i, { model, effort })} />
            <span className="grow" />
            <button className="icon-button" title="Remove" disabled={reviewers.length === 1} onClick={() => setReviewers(reviewers.filter((_, j) => j !== i))}>
              <Icon name="x" size={14} />
            </button>
          </div>
        ))}
        {reviewers.length < 4 && (
          <div className="list-row">
            <button className="button small" onClick={() => setReviewers([...reviewers, { backend: "claude", model: null, effort: "high" }])}>
              <Icon name="plus" size={13} />
              Add a reviewer
            </button>
          </div>
        )}
      </div>
      {error && <p className="error-text">{error}</p>}
    </section>
  );
}
