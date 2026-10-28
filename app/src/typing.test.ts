import { describe, expect, it } from "vitest";
import { argumentOf, commandsFor, finishMention, insertMention, mentionAt, mentionTyped, pickerKey } from "./typing";

describe("slash commands", () => {
  const all = () => true;
  const names = (text: string, can: (name: string) => boolean = all) => commandsFor(text, can).map((c) => c.name);

  it("offers commands for a slash and the start of a name", () => {
    expect(names("/")).toEqual(["new", "group", "remember", "stop", "pause", "resume", "status", "usage"]);
    expect(names("/st")).toEqual(["stop", "status"]);
    expect(names("/US")).toEqual(["usage"]);
    expect(names("/nope")).toEqual([]);
  });

  it("offers only what the box can do: no /status without a chat or thread to report on", () => {
    expect(names("/", (n) => n === "usage" || n === "pause")).toEqual(["pause", "usage"]);
  });

  it("keeps offering a command that takes something while you type it, and nothing else after a name", () => {
    expect(names("/new fix the flaky login test")).toEqual(["new"]);
    expect(argumentOf("/remember  we ship on Tuesdays ")).toBe("we ship on Tuesdays");
    expect(names("/status now")).toEqual([]);
    expect(names("see /usage")).toEqual([]);
    expect(names(" /usage")).toEqual([]);
  });
});

describe("mentions in a composer", () => {
  it("sees the name being typed after an @ at the end", () => {
    expect(mentionTyped("hey @rev")).toBe("rev");
    expect(mentionTyped("@")).toBe("");
    expect(mentionTyped("mail me@home")).toBeUndefined();
    expect(mentionTyped("@rev done")).toBeUndefined();
  });

  it("finishes the name with a space after it", () => {
    expect(finishMention("hey @rev", "reviewer")).toBe("hey @reviewer ");
  });

  it("moves through suggestions with arrows and chooses the highlighted name", () => {
    const names = ["chief", "engineer", "reviewer"];
    let pick = 0;
    pick = pickerKey("ArrowDown", false, pick, names.length) as number;
    expect(pick).toBe(1);
    expect(pickerKey("Enter", false, pick, names.length)).toBe("choose");
    expect(finishMention("hey @", names[pick])).toBe("hey @engineer ");
    pick = pickerKey("ArrowUp", false, pick, names.length) as number;
    pick = pickerKey("ArrowUp", false, pick, names.length) as number;
    expect(pick).toBe(2);
    expect(pickerKey("Tab", false, pick, names.length)).toBe("choose");
    expect(finishMention("@", names[pick])).toBe("@reviewer ");
    expect(pickerKey("Escape", false, pick, names.length)).toBe("close");
    expect(pickerKey("Enter", true, pick, names.length)).toBeUndefined();
    expect(pickerKey("ArrowDown", false, 8, 2)).toBe(0);
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
