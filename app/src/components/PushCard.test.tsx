import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PushCard } from "./PushCard";
import { build } from "./threadBlocks";
import type { StoredEvent } from "../types";

describe("push proposals", () => {
  const event: StoredEvent = { id: 1, task_id: "chat", kind: "push_proposed", at: "", data: {
    project: "demo", summary: "Ready", branch: "main", upstream: "origin/main", push_url_display: "example.com/project.git", commits: ["abc123 Add feature"],
  } };
  it("shows the branch and commits before the button", () => {
    const block = build([event], [], new Map())[0];
    if (block.kind !== "push") throw new Error("missing push card");
    const html = renderToStaticMarkup(<PushCard block={block} />);
    expect(html).toContain("Push demo?");
    expect(html).toContain("origin/main");
    expect(html).toContain("example.com/project.git");
    expect(html).toContain("abc123 Add feature");
    expect(html).toContain(">Push</button>");
  });
  it("shows the CI result", () => {
    const block = build([{ ...event, data: { ...event.data, ci: { note: "CI passed locally on abc123def456", steps: ["a", "b"] } } }], [], new Map())[0];
    if (block.kind !== "push") throw new Error("missing push card");
    expect(renderToStaticMarkup(<PushCard block={block} />)).toContain("CI passed locally on abc123def456 (2 steps)");
  });
  it("says a card a newer one replaced is replaced, with no button", () => {
    const replaced: StoredEvent = { id: 3, task_id: "chat", kind: "push_replaced", at: "", data: { proposal: 1, by: 2 } };
    const block = build([event, { ...event, id: 2 }, replaced], [], new Map())[0];
    if (block.kind !== "push") throw new Error("missing push card");
    const html = renderToStaticMarkup(<PushCard block={block} />);
    expect(html).toContain("Replaced by a newer card");
    expect(html).not.toContain(">Push</button>");
  });
  it("shows the rejection on the card and removes the button", () => {
    const result: StoredEvent = { id: 2, task_id: "chat", kind: "push_result", at: "", data: { proposal: 1, ok: false, reason: "The remote changed; make a new proposal." } };
    const block = build([event, result], [], new Map())[0];
    if (block.kind !== "push") throw new Error("missing push card");
    const html = renderToStaticMarkup(<PushCard block={block} />);
    expect(html).toContain("The remote changed; make a new proposal.");
    expect(html).not.toContain(">Push</button>");
  });
  it("shows the empty state without claiming a push occurred", () => {
    const block = build([{ ...event, data: { ...event.data, commits: [] } }, {
      id: 2, task_id: "chat", kind: "push_result", at: "", data: { proposal: 1, ok: true, pushed: false, reason: "Everything up-to-date. Nothing was pushed." },
    }], [], new Map())[0];
    if (block.kind !== "push") throw new Error("missing push card");
    const html = renderToStaticMarkup(<PushCard block={block} />);
    expect(html).toContain("Nothing was pushed");
    expect(html).not.toContain("Pushed demo");
  });
  it("refuses a previously started proposal after reload", () => {
    const started: StoredEvent = { id: 2, task_id: "chat", kind: "push_started", at: "", data: { proposal: 1 } };
    const block = build([event, started], [], new Map())[0];
    if (block.kind !== "push") throw new Error("missing push card");
    const html = renderToStaticMarkup(<PushCard block={block} />);
    expect(html).toContain("result unknown");
    expect(html).not.toContain(">Push</button>");
  });
});
