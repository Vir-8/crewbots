import { describe, expect, it } from "vitest";
import { MAX_HITS, findHits, parse } from "./PullDiff";

const DIFF = [
  "diff --git a/src/app.py b/src/app.py",
  "index 1111111..2222222 100644",
  "--- a/src/app.py",
  "+++ b/src/app.py",
  "@@ -10,3 +10,4 @@ def main():",
  " keep",
  "-old line",
  "+new line",
  "+another",
  " tail",
  "\\ No newline at end of file",
  "diff --git a/docs/new.md b/docs/new.md",
  "new file mode 100644",
  "--- /dev/null",
  "+++ b/docs/new.md",
  "@@ -0,0 +1 @@",
  "+# New",
  "diff --git a/logo.png b/logo.png",
  "Binary files a/logo.png and b/logo.png differ",
].join("\n");

describe("parsing a diff", () => {
  it("numbers each side's lines from the hunk header", () => {
    const [app] = parse(DIFF);
    expect(app.path).toBe("src/app.py");
    expect(app.status).toBe("changed");
    expect(app.rows.map((r) => [r.kind, r.old ?? null, r.new ?? null])).toEqual([
      ["hunk", null, null],
      ["ctx", 10, 10],
      ["del", 11, null],
      ["add", null, 11],
      ["add", null, 12],
      ["ctx", 12, 13],
    ]);
    expect([app.added, app.removed]).toEqual([2, 1]);
  });

  it("knows new, deleted and binary files", () => {
    const [, added, image] = parse(DIFF);
    expect(added.status).toBe("added");
    expect(added.rows.at(-1)).toMatchObject({ kind: "add", text: "# New", new: 1 });
    expect(image.binary).toBe(true);
    expect(parse("diff --git a/x b/x\ndeleted file mode 100644\n")[0].status).toBe("deleted");
  });

  it("takes anything at all without hanging", () => {
    const parts = ["diff --git a/x b/y", "@@ -1 +1 @@", "@@ -a +b @@", "+", "-", " ", "\\", "", "Binary files", "+++", "---"];
    let seed = 11;
    const random = () => (seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31;
    for (let n = 0; n < 2000; n++) {
      const diff = Array.from({ length: Math.floor(random() * 40) }, () => parts[Math.floor(random() * parts.length)] + (random() < 0.5 ? "x" : "")).join(random() < 0.5 ? "\n" : "\r\n");
      expect(() => parse(diff)).not.toThrow();
    }
  });
});

describe("find in a diff", () => {
  const files = parse(DIFF);

  it("finds every match, in names and lines, ignoring case", () => {
    expect(findHits(files, "new")).toEqual([
      { file: 0, row: 3, at: 0 },
      { file: 1, row: -1, at: 5 },
      { file: 1, row: 1, at: 2 },
    ]);
    expect(findHits(files, "line").length).toBe(2);
    expect(findHits(files, "")).toEqual([]);
  });

  it("finds overlapping text once per place, moving on past each", () => {
    const [file] = parse("diff --git a/a b/a\n@@ -1 +1 @@\n+aaaa");
    expect(findHits([file], "aa").filter((h) => h.row >= 0).map((h) => h.at)).toEqual([0, 2]);
  });

  it("stops at its cap on a huge diff", () => {
    const big = parse(`diff --git a/a b/a\n@@ -1 +1,20000 @@\n${"+e e e\n".repeat(20000)}`);
    expect(findHits(big, "e")).toHaveLength(MAX_HITS);
  });
});
