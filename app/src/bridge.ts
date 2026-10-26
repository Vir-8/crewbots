// The window's line to the Tauri shell (src-tauri/src/crew.rs), which holds the daemon's token and makes every call.

import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { openPath, revealItemInDir } from "@tauri-apps/plugin-opener";
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
  openPath: (path: string) => openPath(path),
  onStatus: (callback: (status: ConnectionStatus) => void) => subscribe("crew:status", callback),
  onLive: (callback: (message: Live) => void) => subscribe("crew:live", callback),
  onResync: (callback: () => void) => subscribe<null>("crew:resync", () => callback()),
};

/** Inside the app, the real shell. In a plain browser, the design preview's made-up crew. */
export const inTauri = "__TAURI_INTERNALS__" in window;
export const bridge: typeof tauriBridge = inTauri ? tauriBridge : (previewBridge as unknown as typeof tauriBridge);
