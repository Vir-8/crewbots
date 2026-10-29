import { useContext, useEffect, useRef, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { RoomBubble } from "./ChatView";

vi.mock("react", async (original) => {
  const react = await original<typeof import("react")>();
  return { ...react, useState: vi.fn(react.useState), useRef: vi.fn(react.useRef), useEffect: vi.fn(react.useEffect), useContext: vi.fn(react.useContext) };
});

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function bubble(text: string, height: number, finding = "") {
  let open = false;
  let long = false;
  const node = { scrollHeight: height };
  vi.stubGlobal("getComputedStyle", () => ({ lineHeight: "20px" }));
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const draw = () => {
    vi.mocked(useState).mockReturnValueOnce([open, (value) => { open = value === true; }]);
    vi.mocked(useState).mockReturnValueOnce([long, (value) => { long = value === true; }]);
    vi.mocked(useRef).mockReturnValueOnce({ current: node });
    vi.mocked(useContext).mockReturnValueOnce(finding);
    vi.mocked(useEffect).mockImplementationOnce((effect) => { effect(); });
    return RoomBubble({ text, onOpen: vi.fn() });
  };
  draw();
  return draw;
}

it("folds a long bot message, with Show more and Show less", () => {
  const draw = bubble("A long handover", 400);
  let shown = draw();
  expect(shown.props.children[0].props.className).toBe("room-folded");
  expect(shown.props.children[1].props.children).toBe("Show more");
  shown.props.children[1].props.onClick();
  shown = draw();
  expect(shown.props.children[0].props.className).toBeUndefined();
  expect(shown.props.children[1].props.children).toBe("Show less");
  shown.props.children[1].props.onClick();
  expect(draw().props.children[1].props.children).toBe("Show more");
});

it("leaves a message as it is when folding would hide less than 3 lines", () => {
  const shown = bubble("Done", 220)();
  expect(shown.props.children[0].props.className).toBeUndefined();
  expect(shown.props.children[1]).toBe(false);
});

it("opens by itself when what's being found is in it", () => {
  const shown = bubble("The scene loads, then the needle is set", 400, "needle")();
  expect(shown.props.children[0].props.className).toBeUndefined();
  expect(shown.props.children[1].props.children).toBe("Show less");
});
