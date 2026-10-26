import { describe, expect, it } from "vitest";
import { commandsFor, finishMention, insertMention, mentionAt, mentionTyped } from "./typing";

describe("slash commands", () => {
  const names = (text: string, hasTask = true) => commandsFor(text, hasTask).map((c) => c.name);

  it("offers commands for a slash and the start of a name", () => {
    expect(names("/")).toEqual(["status", "usage"]);
    expect(names("/st")).toEqual(["status"]);
    expect(names("/US")).toEqual(["usage"]);
    expect(names("/nope")).toEqual([]);
  });

  it("offers /status only where there's a chat or thread to report on", () => {
    expect(names("/", false)).toEqual(["usage"]);
  });

  it("offers nothing once it isn't just a command", () => {
    expect(names("/status now")).toEqual([]);
    expect(names("see /usage")).toEqual([]);
    expect(names(" /usage")).toEqual([]);
  });
});

describe("mentions in a group chat", () => {
  it("sees the name being typed after an @ at the end", () => {
    expect(mentionTyped("hey @rev")).toBe("rev");
    expect(mentionTyped("@")).toBe("");
    expect(mentionTyped("mail me@home")).toBeUndefined();
    expect(mentionTyped("@rev done")).toBeUndefined();
  });

  it("finishes the name with a space after it", () => {
    expect(finishMention("hey @rev", "reviewer")).toBe("hey @reviewer ");
  });
});

describe("mentions in a review comment", () => {
  it("finds the mention the caret is in", () => {
    expect(mentionAt("ping @oc", 8)).toEqual({ start: 5, query: "oc" });
    expect(mentionAt("@", 1)).toEqual({ start: 0, query: "" });
    expect(mentionAt("ping @octo and more", 10)).toEqual({ start: 5, query: "octo" });
  });

  it("leaves emails, code and finished mentions alone", () => {
    expect(mentionAt("mail me@x", 9)).toBeNull();
    expect(mentionAt("run `@x", 7)).toBeNull();
    expect(mentionAt("@@x", 3)).toBeNull();
    expect(mentionAt("@octo done", 10)).toBeNull();
  });

  it("puts the login in place of what was typed, and the caret after its space", () => {
    const value = "thanks @oc, merged";
    const at = mentionAt(value, 10)!;
    expect(insertMention(value, at, "octocat")).toEqual({ value: "thanks @octocat , merged", caret: 16 });
  });
});
