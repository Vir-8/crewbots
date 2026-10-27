// Updating crewbots from the app: what an update would bring, starting it, and following it: the daemon checks and
// builds it, then the app installs the build over itself, has the daemon restart once the crew is idle, and restarts
// into it. The daemon gets an update in the checkout ready before it's asked, so updating is mostly installing.
// Shared by the banner and Settings, so both say the same thing.

import { useEffect, useSyncExternalStore } from "react";
import { api, bridge } from "./api";
import type { UpdateOut } from "./api-types";

const CHECK_EVERY_MS = 30 * 60_000; // GitHub is asked at most this often
const FOLLOW_EVERY_MS = 2_000; // while an update runs

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
  return update?.release?.state === "running";
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

/** A build an update made, not installed yet. */
export function ready(update: UpdateOut | null): boolean {
  const release = update?.release;
  return release?.state === "built" && Boolean(release.bundle) && release.commit !== __APP_COMMIT__;
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

/** Puts the update's build in this app's place, has the daemon restart once the crew is idle, and restarts. */
export async function install() {
  const bundle = state.update?.release.bundle;
  if (!bundle || state.installing) return;
  set({ installing: true, error: null });
  try {
    await bridge.installUpdate(bundle);
    await api.restart(true).catch(() => undefined); // the daemon's own turn: once no turn is running
    await bridge.restartApp();
  } catch (e) {
    set({ installing: false, error: String(e).replace(/^Error: /, "") });
  }
}

/** Updates: installs one already made ready, or starts one (or follows the one under way) and installs it when it's
 * built. */
export async function start() {
  set({ error: null });
  if (ready(state.update)) return install();
  try {
    set({ update: await api.startUpdate(), following: true });
  } catch (e) {
    set({ error: String(e).replace(/^Error: /, "") });
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
  );
}
