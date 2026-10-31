import { expect, it } from "vitest";

const sources = import.meta.glob<string>(["./**/*.{ts,tsx}", "!./preview/**", "!./**/*.test.{ts,tsx}"], {
  query: "?raw", import: "default", eager: true,
});

// The app's WebKit view answers window.confirm without showing a dialog, so a check written with it lets everything
// through: deleting a preset, a merge, a revert. bridge.confirm shows a native one.
it("asks through bridge.confirm, never window.confirm", () => {
  expect(Object.keys(sources).length).toBeGreaterThan(20);
  expect(Object.keys(sources).filter((path) => /window\.confirm\(/.test(sources[path]))).toEqual([]);
});
