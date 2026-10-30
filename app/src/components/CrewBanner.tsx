import { useEffect, useState } from "react";
import { api, ApiError, bridge, errorText } from "../api";
import { API_SCHEMA, API_VERSION, type VersionOut } from "../api-types";
import type { Crew } from "../state";
import { install, ready, restartApp, running, start, timeLeft, useUpdates } from "../updates";
import { Icon } from "./Icon";

const VERSION_EVERY_MS = 5 * 60_000;
const INSTALLED_EVERY_MS = 60_000;

/** The daemon itself, when it isn't simply running: reconnecting, restarting once the crew is idle, or a different
 * version from this app. */
export function CrewBanner({ crew, onUpdates }: { crew: Crew; onUpdates: () => void }) {
  const versions = useVersions(crew);
  const ownState = useUpdates();
  const own = ownState.update; // an update to crewbots itself, from its checkout
  const [dismissed, setDismissed] = useState<string | null>(null);

  if (crew.status.state !== "connected") {
    const failed = crew.status.state === "error" || crew.status.state === "no-crew";
    return (
      <div className={`crew-banner ${failed ? "bad" : ""}`} role="status">
        {failed ? <Icon name="alert" size={13} /> : <span className="working-dot" />}
        <span>{crew.status.message?.split("\n")[0] ?? "Reconnecting to the crew…"}</span>
      </div>
    );
  }
  if (crew.crewState?.restarting) {
    const turns = crew.crewState.turns_running;
    return (
      <div className="crew-banner" role="status">
        <Icon name="repeat" size={13} />
        <span>
          Restarting when the crew is idle
          {turns > 0 && <span className="faint"> · {turns === 1 ? "1 turn" : `${turns} turns`} still running</span>}
        </span>
        <button className="text-button" onClick={() => crew.restart(false)} title="Turns cut off carry on after the restart">
          Restart now
        </button>
        <button className="text-button" onClick={() => crew.cancelRestart()}>
          Cancel
        </button>
      </div>
    );
  }
  if (running(own) || ownState.installing) {
    const left = ownState.installing ? "" : timeLeft(own);
    const after = own?.needs?.app ? "then the app restarts" : "then the crew's daemon restarts";
    return (
      <div className="crew-banner" role="status">
        <span className="working-dot" />
        <span>
          Updating crewbots: {ownState.installing ? "installing it" : own!.release.step?.toLowerCase() ?? "starting"}
          <span className="faint"> · {left ? `${left}, ${after}` : own?.needs?.app ? "the app restarts itself when it's done" : "the crew's daemon restarts when it's done"}</span>
        </span>
      </div>
    );
  }
  const failed = own?.available && own.release.state === "failed";
  const update: Update | null = ownState.error ? { text: `Couldn't update crewbots: ${ownState.error}`, bad: true, action: { label: "Try again", run: () => void start() } }
    : versions ?? (
    ready(own) ? { text: own!.needs?.app ? "A new version of crewbots is ready" : "The daemon is ready to restart", why: own!.commits.map((c) => `· ${c.subject}`).join("\n"), action: { label: own!.needs?.app ? "Restart to update" : "Restart daemon", run: () => void install() } }
    : failed ? { text: `The update stopped at ${own!.release.step?.toLowerCase()}`, why: own!.release.log ?? undefined, bad: true, action: { label: "Try again", run: () => void start() } }
    : own?.available ? { text: "A new version of crewbots is available", why: own.commits.map((c) => `· ${c.subject}`).join("\n"), action: { label: "See what's new", run: onUpdates } }
    : null);
  if (!update || dismissed === update.text) return null;
  return (
    <div className={`crew-banner ${update.bad ? "bad" : ""}`} role="status">
      <Icon name={update.bad ? "alert" : "repeat"} size={13} />
      <span title={update.why}>{update.text}</span>
      {update.action && (
        <button className="text-button" onClick={update.action.run}>
          {update.action.label}
        </button>
      )}
      <button className="icon-button small" onClick={() => setDismissed(update.text)} title="Hide until something changes">
        <Icon name="x" size={12} />
      </button>
    </div>
  );
}

interface Update {
  text: string;
  why?: string;
  bad?: boolean;
  action?: { label: string; run: () => void };
}

/** Whether this app, the build installed over it, and the crew's daemon are the same version, and what to do if not. */
function useVersions(crew: Crew): Update | null {
  const connected = crew.status.state === "connected";
  const [daemon, setDaemon] = useState<VersionOut | "unversioned" | null>(null);
  const [installedNewer, setInstalledNewer] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);
  const [relaunchFailure, setRelaunchFailure] = useState<string | null>(null);

  useEffect(() => {
    if (!connected) return;
    const ask = () =>
      api.version().then(setDaemon, (e) => setDaemon(e instanceof ApiError && e.status === 404 ? "unversioned" : null));
    ask();
    const timer = window.setInterval(ask, VERSION_EVERY_MS);
    return () => window.clearInterval(timer);
  }, [connected]);

  useEffect(() => {
    const ask = () => bridge.appInfo().then((info) => {
      setInstalledNewer(info.installed_newer);
      if (info.relaunch_failure) setRelaunchFailure(info.relaunch_failure); // said once: the app clears it when read
    }, () => undefined);
    ask();
    const timer = window.setInterval(ask, INSTALLED_EVERY_MS);
    window.addEventListener("focus", ask);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", ask);
    };
  }, []);

  if (installedNewer) return {
    text: restartError ? `Couldn't restart crewbots: ${restartError}` : "A new version of crewbots is installed",
    bad: Boolean(restartError),
    action: { label: restartError ? "Try again" : "Restart to update", run: () => {
      setRestartError(null);
      void restartApp().catch((e) => setRestartError(errorText(e)));
    } },
  };
  if (relaunchFailure) return { text: relaunchFailure, bad: true };
  if (!daemon) return null;
  const restartDaemon = { label: "Restart it when idle", run: () => void crew.restart(true) };
  if (daemon === "unversioned" || daemon.api < API_VERSION)
    return { text: "The crew's daemon is older than this app", why: "Restarting it runs the crewbots checkout's current code.", action: restartDaemon };
  if (daemon.api > API_VERSION)
    return { text: `This app is too old for the crew's daemon: install a new build`, why: `The app speaks API v${API_VERSION}; the daemon speaks v${daemon.api}.`, bad: true };
  if (daemon.schema_hash === API_SCHEMA) return null;
  return { text: "This app and the crew's daemon are different versions: install a new build of the app", why: `The app was built against API ${API_SCHEMA}; the daemon serves ${daemon.schema_hash}.` };
}
