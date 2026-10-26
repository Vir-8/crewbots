import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import { Icon } from "../components/Icon";
import { Page, Switch } from "../components/ui";
import type { Crew } from "../state";

/** How crewbots itself is set up: starting at login, where the crew lives, GitHub, and code review's local copies. */
export function Settings({ crew }: { crew: Crew }) {
  const [login, setLogin] = useState<boolean | null>(null);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [repos, setRepos] = useState<Record<string, string>>({});
  const [repo, setRepo] = useState("");
  const [repoError, setRepoError] = useState<string | null>(null);
  useEffect(() => {
    bridge.startAtLogin().then(setLogin, () => setLogin(null));
    api.reviewRepos().then(setRepos, () => setRepos({}));
  }, []);

  const problems = [
    crew.routines?.crew_error && { what: "A role card couldn't be read, so the crew carries on as it was", why: crew.routines.crew_error },
    ...Object.entries(crew.routines?.errors ?? {}).map(([file, why]) => ({ what: `routines/${file} couldn't be read`, why })),
  ].filter(Boolean) as { what: string; why: string }[];
  const reviewed = new Set([...(crew.reviewInbox?.direct ?? []), ...(crew.reviewInbox?.mine ?? [])].map((p) => p.repo));
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

      <section className="page-section">
        <h2>Code review</h2>
        <p className="faint small">
          A review reads the pull request's diff. Point a repo at your local copy, and the reviewer reads the pull request's own code too, checked out at its head beside your copy.
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
