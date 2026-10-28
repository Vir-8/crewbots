import { useEffect } from "react";
import { check, install, progress, ready, running, start, timeLeft, useUpdates } from "../updates";
import "../styles/dialogs.css";

/** crewbots → Check for Updates…: checks at once, then says it's up to date, or what an update brings and runs it
 * (the daemon checks and builds, the app installs it over itself and restarts). Closing it doesn't stop an update:
 * the banner keeps following it. */
export function UpdatesDialog({ onClose, restarting = false }: { onClose: () => void; restarting?: boolean }) {
  const { update, checking, installing, error } = useUpdates();
  useEffect(() => {
    check(true);
    const key = (e: KeyboardEvent) => e.key === "Escape" && !e.isComposing && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  const release = update?.release;
  const busy = running(update) || installing;
  const restartsApp = Boolean(update?.needs?.app);
  const stopped = update?.available && release?.state === "failed";
  const changes = update?.commits ?? [];

  let title = "Checking for updates…";
  let text = "";
  let action: { label: string; run: () => void } | null = null;
  if (busy) {
    title = "Updating crewbots…";
    const then = restartsApp ? "Then crewbots installs it and restarts" : "Then the crew's daemon restarts once no bot is working; the app stays open";
    text = installing ? (restartsApp ? "Installing it. crewbots restarts in a moment." : "Restarting the crew's daemon once no bot is working.") : `${release?.step ?? "Starting"}${timeLeft(update) ? `, ${timeLeft(update)}` : ""}. ${then}; you can close this.`;
  } else if (update?.note) {
    title = "crewbots can't update just now";
    text = update.note;
  } else if (restarting && !restartsApp) {
    title = "The daemon is waiting to restart";
    text = "The crew's daemon restarts once no bot is working; the app stays open.";
  } else if (ready(update)) {
    title = restartsApp ? "An update is ready to install" : "The daemon is ready to restart";
    text = restartsApp ? "It's built. crewbots installs it and restarts; the crew's daemon restarts once no bot is working." : "The crew's daemon restarts into it once no bot is working; the app stays open.";
    action = { label: restartsApp ? "Restart to Update" : "Restart Daemon", run: () => void install() };
  } else if (update?.available) {
    title = "A new version of crewbots is available";
    text = `${changes.length === 1 ? "1 change" : `${changes.length} changes`}${update.pull ? ", from GitHub" : ""}:`;
    text += restartsApp ? "" : " (the crew's daemon only: no build, and the app stays open)";
    action = { label: stopped ? "Try Again" : restartsApp ? "Update and Restart" : "Restart Daemon", run: () => void start() };
  } else if (update && !checking) {
    title = "crewbots is up to date";
    text = `This is the latest build (${__APP_COMMIT__ || "a development build"}).`;
  }

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog updates-dialog" role="dialog" aria-label="Updates" onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        {(checking && !update) || busy ? <div className="updates-working"><span className="working-dot" />{text && <span>{text}</span>}</div> : text && <p className="updates-text">{text}</p>}
        {busy && progress(update) !== null && (
          <div className="update-progress" role="progressbar" aria-valuenow={Math.round(progress(update)! * 100)} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${Math.round(progress(update)! * 100)}%` }} />
          </div>
        )}
        {update?.available && !update.note && !restarting && !busy && !ready(update) && (
          <ul className="update-changes">
            {changes.slice(0, 12).map((c) => (
              <li key={c.sha}>{c.subject}</li>
            ))}
            {changes.length > 12 && <li className="faint">and {changes.length - 12} more</li>}
          </ul>
        )}
        {stopped && !update?.note && !restarting && !busy && (
          <>
            <p className="updates-text bad">The last update stopped at {release?.step?.toLowerCase()}.</p>
            {release?.log && <pre className="update-log">{release.log}</pre>}
          </>
        )}
        {error && <p className="updates-text bad">{error}</p>}
        <div className="dialog-actions">
          <button className="button" onClick={onClose}>
            {action ? "Later" : busy ? "Close" : "OK"}
          </button>
          {action && (
            <button className="button primary" onClick={action.run}>
              {action.label}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
