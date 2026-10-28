import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { UpdateOut } from "./api-types";

const { api, bridge } = vi.hoisted(() => ({
  api: { update: vi.fn(), restart: vi.fn(), startUpdate: vi.fn() },
  bridge: { installUpdate: vi.fn(), restartApp: vi.fn() },
}));
vi.mock("./api", () => ({ api, bridge }));

import { check, install, ready, restartApp, useUpdates } from "./updates";

const update: UpdateOut = {
  app: "old", daemon: "old", head: "new", available: true, commits: [], pull: false, note: null,
  needs: { app: true, daemon: true }, release: { state: "built", commit: "new", bundle: "/build/crewbots.app" },
};

function error() {
  function ErrorText() { return <>{useUpdates().error}</>; }
  return renderToStaticMarkup(<ErrorText />);
}

describe("prepared updates", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.update.mockResolvedValue(update);
    api.restart.mockResolvedValue({});
    bridge.installUpdate.mockResolvedValue("/Applications/crewbots.app");
  });

  it("does not offer an older build after the checkout advances", () => {
    expect(ready(update)).toBe(true);
    expect(ready({ ...update, release: { ...update.release, commit: "old" } })).toBe(false);
    expect(ready({ ...update, release: { ...update.release, commit: __APP_COMMIT__ } })).toBe(false);
  });

  it("rechecks before installing and reports a changed checkout", async () => {
    await check();
    api.update.mockResolvedValue({ ...update, head: "later" });
    await install();
    expect(bridge.installUpdate).not.toHaveBeenCalled();
    expect(error()).toContain("checkout changed");
  });

  it("reports a refused daemon restart instead of swallowing it", async () => {
    await check();
    api.restart.mockRejectedValue(new Error("restart refused"));
    await install();
    expect(error()).toContain("restart refused");
    expect(bridge.restartApp).not.toHaveBeenCalled();
    await check();
    expect(error()).toContain("restart refused");
  });

  it("reports a restart that leaves the app open", async () => {
    bridge.restartApp.mockResolvedValue(undefined);
    await expect(restartApp()).rejects.toThrow("The app didn't close");
    vi.useFakeTimers();
    bridge.restartApp.mockReturnValue(new Promise(() => {}));
    const failure = expect(restartApp()).rejects.toThrow("The app didn't close");
    await vi.advanceTimersByTimeAsync(10000);
    await failure;
    vi.useRealTimers();
  });
});
