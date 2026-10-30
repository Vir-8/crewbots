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

it("keeps the other review checkouts when one is removed", async () => {
  await previewBridge.request("PUT", "/reviews/repos/acme/api", { folder: "~/code/acme-api" });
  const left = await previewBridge.request("PUT", "/reviews/repos/acme/web", { folder: null });
  expect(left.data).toEqual({ "acme/api": "~/code/acme-api" });
  expect((await previewBridge.request("GET", "/reviews/repos")).data).toEqual({ "acme/api": "~/code/acme-api" });
});

it("remembers the merge switch", async () => {
  await previewBridge.request("PUT", "/crew/merge-settings", { auto_merge: true });
  expect((await previewBridge.request("GET", "/crew/merge-settings")).data).toEqual({ auto_merge: true });
});

it("imports a server into the crew's and removes it again", async () => {
  const names = async () => ((await previewBridge.request("GET", "/mcp/servers")).data as { name: string }[]).map((s) => s.name);
  expect((await previewBridge.request("POST", "/mcp/import", { id: "c" })).ok).toBe(true);
  expect(await names()).toContain("gimp");
  await previewBridge.request("DELETE", "/mcp/servers/gimp");
  expect(await names()).not.toContain("gimp");
});

it("turns on a proposed routine and runs it as a test, or declines it", async () => {
  type Listed = { name: string; enabled: boolean; proposed_by?: string | null };
  const list = async () => ((await previewBridge.request("GET", "/routines")).data as { routines: Listed[] }).routines;
  const test = await previewBridge.request("POST", "/routines/dependency-check/confirm");
  expect((test.data as { status: string }).status).toBe("running");
  expect((await list()).find((r) => r.name === "dependency-check")).toMatchObject({ enabled: true, proposed_by: null });
  await previewBridge.request("DELETE", "/routines/retro");
  expect((await list()).map((r) => r.name)).not.toContain("retro");
});
