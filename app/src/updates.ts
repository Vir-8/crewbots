// Updating crewbots from the app: what an update would bring, starting it, and following it to the end, when the app
// restarts itself into the new build. Shared by the banner and Settings, so both say the same thing.

import { useEffect, useSyncExternalStore } from "react";
import { api, bridge } from "./api";
import type { UpdateOut } from "./api-types";

const CHECK_EVERY_MS = 30 * 60_000; // GitHub is asked at most this often
const FOLLOW_EVERY_MS = 2_000; // while an update runs

type State = { update: UpdateOut | null; checking: boolean; error: string | null };
let state: State = { update: null, checking: false, error: null };
const listeners = new Set<() => void>();
let startedHere = false; // this app started the update now running: it restarts itself when it's done
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
    const release = update.release;
    if (startedHere && release.state === "done" && release.commit && release.commit !== __APP_COMMIT__) {
      startedHere = false;
      await bridge.restartApp(); // into the build the update just installed
    }
  } catch (e) {
    set({ checking: false, error: String(e).replace(/^Error: /, "") });
  }
  schedule();
}

export async function start() {
  set({ error: null });
  try {
    set({ update: await api.startUpdate() });
    startedHere = true;
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
