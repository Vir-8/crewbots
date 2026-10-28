import { useEffect, useRef, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { AskCard, AskMarkdown } from "./ThreadParts";
import { task } from "../preview/demo/make";

vi.mock("react", async (original) => {
  const react = await original<typeof import("react")>();
  return { ...react, useState: vi.fn(react.useState), useRef: vi.fn(react.useRef), useEffect: vi.fn(react.useEffect) };
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("shows a toggle only for overflow and expands and collapses", () => {
  let expanded = false;
  let long = false;
  const node = { scrollHeight: 160 };
  let measure = () => {};
  vi.stubGlobal("getComputedStyle", () => ({ lineHeight: "20px" }));
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { measure = callback; }
    observe() {}
    disconnect() {}
  });
  const draw = () => {
    vi.mocked(useState).mockReturnValueOnce([expanded, (value) => { expanded = value === true; }]);
    vi.mocked(useState).mockReturnValueOnce([long, (value) => { long = value === true; }]);
    vi.mocked(useRef).mockReturnValueOnce({ current: node });
    vi.mocked(useEffect).mockImplementationOnce((effect) => { effect(); });
    return AskMarkdown({ text: "Long Markdown", onOpen: vi.fn() });
  };
  draw();
  let rendered = draw();
  expect(rendered.props.children[0].props.className).toContain("clamped");
  expect(rendered.props.children[1].props.children).toBe("Show more");
  rendered.props.children[1].props.onClick();
  rendered = draw();
  expect(rendered.props.children[0].props.className).not.toContain("clamped");
  expect(rendered.props.children[1].props.children).toBe("Show less");
  rendered.props.children[1].props.onClick();
  expect(expanded).toBe(false);
  node.scrollHeight = 40;
  measure();
  expect(draw().props.children[1]).toBe(false);
});

it("opens the task on card clicks but not on controls inside it", () => {
  const onOpen = vi.fn();
  const card = AskCard({ bot: "critic", question: "Ready?", task: task("t_ask_test02", "critic", "Test"), onOpen });
  card.props.onClick({ target: { closest: () => ({}) } });
  expect(onOpen).not.toHaveBeenCalled();
  card.props.onClick({ target: { closest: () => null } });
  expect(onOpen).toHaveBeenCalledWith("t_ask_test02");
});
