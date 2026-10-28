import { describe, expect, it } from "vitest";
import { profileVisible, toggleProfile } from "./panelState";

describe("the profile toggle beside a thread", () => {
  for (const previous of [false, true]) {
    it(`shows the profile when the saved preference was ${previous}`, () => {
      const next = toggleProfile(previous, true);
      expect(next).toEqual({ profileOn: true, thread: null, page: null });
      expect(profileVisible(next.profileOn, Boolean(next.thread), Boolean(next.page))).toBe(true);
      expect(toggleProfile(next.profileOn, false).profileOn).toBe(false);
    });

    it(`restores the ${previous ? "open" : "closed"} profile when the thread is closed`, () => {
      expect(profileVisible(previous, true, false)).toBe(false);
      expect(profileVisible(previous, false, false)).toBe(previous);
    });
  }

  it("shows the profile when a page is open", () => {
    expect(toggleProfile(true, true).profileOn).toBe(true);
    expect(toggleProfile(false, true).profileOn).toBe(true);
  });
});
