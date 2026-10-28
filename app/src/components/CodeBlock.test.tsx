import { useEffect, useRef, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { CodeBlock, Markdown } from "./bits";

vi.mock("react", async (original) => {
  const react = await original<typeof import("react")>();
  return { ...react, useState: vi.fn(react.useState), useRef: vi.fn(react.useRef), useEffect: vi.fn(react.useEffect) };
});

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.restoreAllMocks(); });

it("copies only the fenced code and shows confirmation briefly", async () => {
  vi.useFakeTimers();
  const writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal("window", { setTimeout, clearTimeout });
  let copied = false;
  const timer = { current: undefined };
  const draw = (text: string) => {
    vi.mocked(useState).mockReturnValueOnce([copied, (next) => { copied = next === true; }]);
    vi.mocked(useRef).mockReturnValueOnce(timer);
    vi.mocked(useEffect).mockImplementationOnce(() => undefined);
    return CodeBlock({ text });
  };
  const markdown = Markdown({ text: "Before\n```ts\n  first()\nsecond()\n```\nAfter", onSelect: () => undefined });
  const block = markdown.props.children[1];
  expect(block.type).toBe(CodeBlock);
  const text = block.props.text;
  const button = draw(text).props.children[0].props.children;
  expect(button.props.children).toBe("Copy");
  button.props.onClick();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  expect(writeText).toHaveBeenCalledWith("  first()\nsecond()");
  expect(draw(text).props.children[0].props.children.props.children).toBe("Copied");
  vi.advanceTimersByTime(1500);
  expect(draw(text).props.children[0].props.children.props.children).toBe("Copy");
});
