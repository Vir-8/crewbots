import { expect, it } from "vitest";
import { previewBridge } from "./bridge";

it("withdraws a demo proposal and records the reason", async () => {
  const path = "/tasks/t_login00002";
  const invalid = await previewBridge.request("POST", `${path}/withdraw-merge`, { reason: " " });
  expect(invalid.ok).toBe(false);
  const response = await previewBridge.request("POST", `${path}/withdraw-merge`, { reason: "  Superseded  " });
  expect(response.ok).toBe(true);
  expect((response.data as { merge_state: string | null }).merge_state).toBeNull();
  const events = await previewBridge.request("GET", `${path}/events`);
  expect(events.data).toEqual(expect.arrayContaining([expect.objectContaining({
    kind: "merge_withdrawn", data: { by: "user", reason: "Superseded" },
  })]));
  expect((await previewBridge.request("POST", `${path}/withdraw-merge`, { reason: "Again" })).ok).toBe(false);
});
