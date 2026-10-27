import { describe, expect, it } from "vitest";
import { shownAs } from "./components/Attachments";
import { highlight } from "./highlight";

const kinds = (code: string, ext: string) => highlight(code, ext).filter((t) => t.kind).map((t) => `${t.kind}:${t.text}`);

describe("highlighting a shared file", () => {
  it("colours comments, strings, numbers and keywords, each language its own way", () => {
    expect(kinds('def f(x):  # the edge\n    return "a" + 2', "py")).toEqual(["keyword:def", "comment:# the edge", "keyword:return", 'string:"a"', "number:2"]);
    expect(kinds("const n = 0x1f; // hex", "ts")).toEqual(["keyword:const", "number:0x1f", "comment:// hex"]);
    expect(kinds("select id from t -- all", "sql")).toEqual(["keyword:select", "keyword:from", "comment:-- all"]);
    expect(kinds("x = '#not a comment'", "py")).toEqual(["string:'#not a comment'"]);
  });

  it("leaves prose and data alone, and keeps every character", () => {
    expect(highlight("if this is a note, it stays plain", "md")).toEqual([{ text: "if this is a note, it stays plain" }]);
    const code = "fn main() {\n    let s = \"hi\"; /* two\nlines */\n}";
    expect(highlight(code, "rs").map((t) => t.text).join("")).toBe(code);
  });
});

describe("how a file is shown", () => {
  it("goes by extension first, then by MIME type", () => {
    expect(shownAs({ name: "app.ts", mime: "video/mp2t" })).toBe("text"); // TypeScript, not a video stream
    expect(shownAs({ name: "cut.mov", mime: "video/quicktime" })).toBe("video");
    expect(shownAs({ name: "chime.m4a", mime: "audio/mp4" })).toBe("audio");
    expect(shownAs({ name: "chart.svg", mime: "image/svg+xml" })).toBe("image"); // drawn as a picture: its scripts never run
    expect(shownAs({ name: "notes.pdf", mime: "application/pdf" })).toBe("pdf");
    expect(shownAs({ name: "report.html", mime: "text/html" })).toBe("html");
    expect(shownAs({ name: "data.json", mime: "application/json" })).toBe("text");
    expect(shownAs({ name: "Dockerfile", mime: "application/octet-stream" })).toBe("text");
    expect(shownAs({ name: "scene.blend", mime: "application/octet-stream" })).toBe("other");
  });
});
