import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ReplaceMainCard } from "./ReplaceMainCard";
import { build } from "./threadBlocks";
import type { StoredEvent } from "../types";

describe("replace-main proposals", () => {
  const checks = [{ name: "Same tree", ok: true, detail: "the trees are identical" }, { name: "Folder is clean", ok: true, detail: "no uncommitted changes" }];
  const event: StoredEvent = { id: 1, task_id: "chat", kind: "replace_main_proposed", at: "", data: {
    project: "demo", summary: "Squash", target: "main", branch: "crewbots/clean-history", upstream: "origin/main", backup: "refs/backup/main-2026-10-28",
    old_commits: 40, new_commits: 6, checks,
  } };
  const card = (events: StoredEvent[]) => {
    const block = build(events, [], new Map())[0];
    if (block.kind !== "replace") throw new Error("missing replace card");
    return renderToStaticMarkup(<ReplaceMainCard block={block} />);
  };
  it("shows the backup, the counts and the checks before the one button", () => {
    const html = card([event]);
    expect(html).toContain("refs/backup/main-2026-10-28");
    expect(html).toContain("40 now, 6 after");
    expect(html).toContain("Same tree");
    expect(html).toContain(">Replace</button>");
  });
  it("shows a refusal with no button", () => {
    const html = card([{ ...event, data: { ...event.data, error: "Folder is clean: there are uncommitted changes", checks: [{ ...checks[1], ok: false, detail: "there are uncommitted changes" }] } }]);
    expect(html).toContain("there are uncommitted changes");
    expect(html).not.toContain(">Replace</button>");
  });
  it("shows the result and drops the button, also after a reload", () => {
    const done: StoredEvent = { id: 2, task_id: "chat", kind: "replace_main_result", at: "", data: { proposal: 1, ok: true, reason: "main now points at abc." } };
    expect(card([event, done])).toContain("main now points at abc.");
    expect(card([event, done])).not.toContain(">Replace</button>");
    const started: StoredEvent = { id: 2, task_id: "chat", kind: "replace_main_started", at: "", data: { proposal: 1 } };
    expect(card([event, started])).toContain("result unknown");
    expect(card([event, started])).not.toContain(">Replace</button>");
  });
});
