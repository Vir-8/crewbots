import { useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { bridge } from "../bridge";
import type { Attachment, StoredEvent } from "../types";
import { build, fold } from "./threadBlocks";
import { Steps } from "./ThreadParts";

vi.mock("react", async (original) => {
  const react = await original<typeof import("react")>();
  return { ...react, useState: vi.fn(react.useState) };
});

afterEach(() => vi.restoreAllMocks());

describe("images returned by tools", () => {
  it("keeps the result in its steps and renders its image only while the steps are open", () => {
    vi.spyOn(bridge, "fileSrc").mockImplementation((path) => path);
    const image: Attachment = { id: "read-image", name: "read.png", path: "/attachments/read.png", mime: "image/png", size: 100, kind: "image" };
    const event = (id: number, kind: string, data: StoredEvent["data"]): StoredEvent => ({ id, kind, data, task_id: "chat", at: "2026-10-27T07:00:00Z" });
    const blocks = fold(build([
      event(1, "text", { text: "I'll read the screenshot." }),
      event(2, "tool_call", { id: "read", name: "Read", input: { file_path: image.path } }),
      event(3, "tool_result", { id: "read", output: "", images: [image] }),
      event(4, "text", { text: "Checked." }),
    ], [], new Map()));
    expect(blocks.map((b) => b.kind)).toEqual(["say", "steps", "say"]);
    const steps = blocks[1];
    if (steps.kind !== "steps") throw new Error("Expected the Read step");
    expect(steps.calls[0].result?.data.images).toEqual([image]);

    // These tests run in Node: keep the Steps hook's state across renders and exercise its actual click handler.
    // Descendants still use React's real hooks during the server render.
    let open = false;
    const draw = () => {
      vi.mocked(useState).mockReturnValueOnce([open, (next) => { open = next === true; }]);
      return Steps({ calls: steps.calls });
    };
    const closed = draw();
    expect(renderToStaticMarkup(closed)).not.toContain("<img");
    closed.props.children[0].props.onClick();
    const opened = draw();
    expect(renderToStaticMarkup(opened)).toContain('alt="read.png"');
    opened.props.children[0].props.onClick();
    expect(renderToStaticMarkup(draw())).not.toContain("<img");
  });
});
