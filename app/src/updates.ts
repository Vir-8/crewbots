// Updating crewbots from the app: what an update would bring, starting it, and following it. It does only what it
// needs: the daemon runs from the checkout, so a change to it is a restart once the crew is idle, with the app staying
// open; a change to the app is built again (incrementally) and installed over this one, which restarts into it.
// Shared by the banner and Settings, so both say the same thing.

import { useEffect, useSyncExternalStore } from "react";
import { api, bridge, errorText } from "./api";
import type { UpdateOut } from "./api-types";

const CHECK_EVERY_MS = 30 * 60_000; // GitHub is asked at most this often
const FOLLOW_EVERY_MS = 2_000; // while an update runs
const CHECKOUT_CHANGED = "The checkout changed since this update was prepared. Check for updates again.";
const DIDNT_CLOSE = "The app didn't close. Quit and reopen it.";

// `following`: you asked for the update now under way, here: the app installs its build when it's ready.
type State = { update: UpdateOut | null; checking: boolean; installing: boolean; following: boolean; error: string | null };
let state: State = { update: null, checking: false, installing: false, following: false, error: null };
const listeners = new Set<() => void>();
let timer: number | undefined;
let users = 0;

function set(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

export function running(update: UpdateOut | null): boolean {
  return update?.release.state === "running";
}

/** Asks the daemon what an update would bring; `fetch` asks GitHub too. */
export async function check(fetch = false) {
  set({ checking: true });
  try {
    const update = await api.update(fetch);
    set({ update, checking: false, error: null });
    if (state.following && ready(update)) {
      set({ following: false });
      await install();
    }
  } catch {
    set({ checking: false }); // the daemon away a moment (restarting, say): asked again on the next round
  }
  schedule();
}

/** An update got ready and not taken in yet: a build of the app to install, or the daemon to restart. */
export function ready(update: UpdateOut | null): boolean {
  if (!update) return false;
  const release = update.release;
  return release.state === "built" && update.available
    && (update.pull || Boolean(update.head && release.commit === update.head))
    && (Boolean(release.bundle) || !update.needs?.app)
    && (!update.needs?.app || release.commit !== __APP_COMMIT__);
}

/** A build of the app to put in this one's place, rather than only the daemon to restart. */
function newApp(update: UpdateOut | null): boolean {
  if (!update) return false;
  return Boolean(update.needs?.app && update.release.bundle) && update.release.commit !== __APP_COMMIT__;
}

/** How far along an update's build is, from how long the last one took: null with nothing to go by. */
export function progress(update: UpdateOut | null): number | null {
  const release = update?.release;
  if (release?.state !== "running" || !release.expected || !release.started_at) return null;
  return Math.min(0.95, Math.max(0, Date.now() / 1000 - release.started_at) / release.expected);
}

/** How long an update's build should take yet, in words: "about 40 seconds left". */
export function timeLeft(update: UpdateOut | null): string {
  const release = update?.release;
  if (progress(update) === null || !release?.expected || !release.started_at) return "";
  const left = Math.round(release.expected - (Date.now() / 1000 - release.started_at));
  return left > 90 ? `about ${Math.round(left / 60)} minutes left` : left > 10 ? `about ${Math.round(left / 5) * 5} seconds left` : "almost done";
}

/** A successful restart closes this window. If it stays open, surface the failure instead of waiting forever. */
export async function restartApp() {
  let closing: number | undefined;
  try {
    await Promise.race([
      bridge.restartApp(),
      new Promise<never>((_, reject) => { closing = window.setTimeout(() => reject(new Error(DIDNT_CLOSE)), 10000); }),
    ]);
    throw new Error(DIDNT_CLOSE);
  } finally {
    window.clearTimeout(closing);
  }
}

/** Takes the update in: puts a new build of the app in this one's place, has the daemon restart once the crew is
 * idle (when its code changed), and restarts the app (when it was built again). */
export async function install() {
  const update = state.update;
  if (!update || !ready(update) || state.installing) return;
  set({ installing: true, error: null });
  try {
    const current = await api.update();
    set({ update: current });
    if (current.note || !current.available) {
      set({ installing: false, following: false });
      return;
    }
    if (!ready(current) || current.release.commit !== update.release.commit) {
      throw new Error(CHECKOUT_CHANGED);
    }
    if (newApp(current)) {
      const result = await bridge.installUpdate(current.release.bundle!);
      if (result.warning) console.warn(`crewbots update: ${result.warning}`);
    }
    if (current.needs?.daemon) await api.restart(true); // the daemon's own turn: once no turn runs
    if (newApp(current)) await restartApp();
    else set({ installing: false }); // the app stays open, and reconnects when the daemon's back
  } catch (e) {
    set({ installing: false, error: errorText(e) });
  }
}

/** Updates: takes in one that's ready, or starts one (or follows the one under way) and takes it in when it is. */
export async function start() {
  set({ error: null });
  if (ready(state.update)) return install();
  try {
    set({ update: await api.startUpdate(), following: true });
  } catch (e) {
    set({ error: errorText(e) });
  }
  schedule();
}

function schedule() {
  window.clearTimeout(timer);
  if (!users) return;
  timer = window.setTimeout(() => check(!running(state.update)), running(state.update) ? FOLLOW_EVERY_MS : CHECK_EVERY_MS);
}

/** The update's state, kept current while something shows it: checked now, then every half hour, and every couple of
 * seconds while one runs. */
export function useUpdates(): State {
  useEffect(() => {
    users++;
    if (users === 1) check(true);
    return () => {
      users--;
      if (!users) window.clearTimeout(timer);
    };
  }, []);
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => state,
    () => state,
  );
}
