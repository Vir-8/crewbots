// The window's line to the Tauri shell (src-tauri/src/crew.rs), which holds the daemon's token and makes every call.

import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { openPath, openUrl, revealItemInDir } from "@tauri-apps/plugin-opener";
import { previewBridge } from "./preview/bridge";
import type { ConnectionStatus, Live } from "./types";

export interface Reply {
  ok: boolean;
  status: number;
  data: any;
}

function subscribe<T>(channel: string, callback: (payload: T) => void): () => void {
  let unlisten: (() => void) | undefined;
  let closed = false;
  listen<T>(channel, (event) => callback(event.payload)).then((off) => (closed ? off() : (unlisten = off)));
  return () => {
    closed = true;
    unlisten?.();
  };
}

const tauriBridge = {
  status: () => invoke<ConnectionStatus>("crew_status"),
  request: (method: string, path: string, body?: unknown) =>
    invoke<Reply>("crew_request", { method, path, body: body ?? null }),
  pickFolder: async () => {
    const picked = await open({ directory: true });
    return typeof picked === "string" ? picked : null;
  },
  reveal: (path: string) => revealItemInDir(path),
  /** Whether the crew starts at login; pass `enabled` to change it. */
  startAtLogin: (enabled?: boolean) => invoke<boolean>("start_at_login", { enabled: enabled ?? null }),
  /** Whether a newer build of the app is installed over this one, and why the last restart failed (said once). */
  appInfo: () => invoke<{ installed_newer: boolean; relaunch_failure: string | null }>("app_info"),
  /** Puts the build an update made in this app's place (the app restarts after). */
  installUpdate: (bundle: string) => invoke<{ path: string; warning: string | null }>("install_update", { bundle }),
  /** Quits and opens the app again, from the build installed now. */
  restartApp: () => invoke<void>("restart_app"),
  openPath: (path: string) => openPath(path),
  /** Where the window draws a file from: straight from disk, through the asset protocol (no base64, and video
   * streams). The crew's attachments are open to it; a file elsewhere needs `showFiles` first. */
  fileSrc: (path: string) => convertFileSrc(path),
  showFiles: (paths: string[]) => invoke<void>("show_files", { paths }),
  /** Opens a file in its own app; one that could run (a script, an app) is shown in Finder instead. */
  openFile: (path: string) => invoke<void>("open_file", { path }),
  /** Quick Look's thumbnail of a file (a PDF's first page), as a path to draw, or null. */
  thumbnail: (path: string, size: number) => invoke<string | null>("thumbnail", { path, size }),
  /** A GitHub page, in your browser. */
  openUrl: (url: string) => openUrl(url),
  onStatus: (callback: (status: ConnectionStatus) => void) => subscribe("crew:status", callback),
  onLive: (callback: (message: Live) => void) => subscribe("crew:live", callback),
  onResync: (callback: () => void) => subscribe<null>("crew:resync", () => callback()),
  /** The crewbots menu in the menu bar: "check-updates" or "settings". */
  onMenu: (callback: (item: string) => void) => subscribe<string>("app:menu", callback),
  // Dictation through macOS's on-device speech recognizer, in the shell (src-tauri/src/dictation.rs).
  dictation: {
    start: () => invoke<void>("dictation_start"),
    stop: () => invoke<void>("dictation_stop"),
    /** Everything heard since `start`, as it firms up. */
    onHeard: (callback: (text: string) => void) => subscribe<string>("dictation:heard", callback),
    /** Listening ended: you stopped it, it heard the end, or it couldn't listen (with why). */
    onStopped: (callback: (error: string | null) => void) => subscribe<string | null>("dictation:stopped", callback),
  },
};

/** Inside the app, the real shell. In a plain browser, the design preview's made-up crew. */
export const inTauri = "__TAURI_INTERNALS__" in window;
export const bridge: typeof tauriBridge = inTauri ? tauriBridge : (previewBridge as unknown as typeof tauriBridge);
