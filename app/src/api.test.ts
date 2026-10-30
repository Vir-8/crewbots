import { describe, expect, it, vi } from "vitest";

const { bridge } = vi.hoisted(() => ({ bridge: { request: vi.fn() } }));
vi.mock("./bridge", () => ({ bridge }));

import { api, ApiError, errorText } from "./api";

describe("a refused request", () => {
  it("carries the daemon's detail and status", async () => {
    bridge.request.mockResolvedValue({ ok: false, status: 404, data: { detail: "Not Found" } });
    const e = await api.version().catch((e: unknown) => e);
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).status).toBe(404);
    expect(errorText(e)).toBe("Not Found");
  });

  it("says its status when the daemon gave no detail", async () => {
    bridge.request.mockResolvedValue({ ok: false, status: 502, data: null });
    expect(errorText(await api.version().catch((e: unknown) => e))).toBe("request failed (502)");
  });
});

it("shows a rejection that isn't an Error as it is", () => {
  expect(errorText("crew_status failed")).toBe("crew_status failed");
});
