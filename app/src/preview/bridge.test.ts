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
  expect(await names()).toContain("postgres");
  await previewBridge.request("DELETE", "/mcp/servers/postgres");
  expect(await names()).not.toContain("postgres");
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

it("changes several bots' models at once, or none of them, and marks every preset the crew matches", async () => {
  type Listed = { presets: { name: string }[]; in_use: string[] };
  const presets = async () => (await previewBridge.request("GET", "/presets")).data as Listed;
  const engineer = async () => ((await previewBridge.request("GET", "/bots")).data as { name: string }[]).find((b) => b.name === "engineer");
  expect(await presets()).toMatchObject({ presets: [{ name: "Everyday" }, { name: "Codex is low" }], in_use: ["Everyday"] });

  const bad = await previewBridge.request("PUT", "/bots/models", { bots: { engineer: { backend: "claude" }, critic: { effort: "enormous" } } });
  expect(bad.status).toBe(422);
  expect(await engineer()).toMatchObject({ backend: "codex" });

  const good = await previewBridge.request("PUT", "/bots/models", { bots: { engineer: { backend: "claude", model: "sonnet" }, critic: { team: "Critics" } } });
  expect(good.data).toEqual({ changed: ["engineer", "critic"] });
  expect(await engineer()).toMatchObject({ backend: "claude", model: "sonnet", team: "Engineers" });
  expect((await presets()).in_use).toEqual([]);

  const back = await previewBridge.request("PUT", "/bots/models", { bots: { engineer: { backend: "codex", model: null, effort: null } } });
  expect(back.data).toEqual({ changed: ["engineer"] });
  expect(await engineer()).toMatchObject({ backend: "codex", model: null });
  expect((await presets()).in_use).toEqual(["Everyday"]);
  await previewBridge.request("PUT", "/presets/Same%20again");
  expect((await presets()).in_use).toEqual(["Everyday", "Same again"]);
  await previewBridge.request("DELETE", "/presets/Same%20again");
});

it("saves the crew as a preset, and deletes it", async () => {
  const saved = await previewBridge.request("PUT", "/presets/Light%20week");
  expect((saved.data as { presets: { name: string }[] }).presets.map((p) => p.name)).toContain("Light week");
  const left = await previewBridge.request("DELETE", "/presets/Light%20week");
  expect((left.data as { presets: { name: string }[] }).presets.map((p) => p.name)).not.toContain("Light week");
  expect((await previewBridge.request("DELETE", "/presets/Light%20week")).status).toBe(404);
});
