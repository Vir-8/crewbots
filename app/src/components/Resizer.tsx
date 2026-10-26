import { useLayoutEffect, useRef, useState } from "react";

const STEP = 16; // an arrow key's move; with Shift, four times that

/** A width the user dragged a pane to, kept across restarts; null means the pane's own default. */
export function useWidth(key: string): [number | null, (width: number | null) => void] {
  const [width, setWidth] = useState<number | null>(() => {
    try {
      const saved = Number(localStorage.getItem(`crewbots.width.${key}`));
      return saved > 0 ? saved : null;
    } catch {
      return null;
    }
  });
  const set = (next: number | null) => {
    setWidth(next);
    try {
      if (next === null) localStorage.removeItem(`crewbots.width.${key}`);
      else localStorage.setItem(`crewbots.width.${key}`, String(next));
    } catch {
      // not kept, but still applied
    }
  };
  return [width, set];
}

/**
 * The edge of a pane, to drag it wider or narrower. The width lives in a CSS variable on `host` (the grid the pane is
 * part of), set straight on the element while dragging so nothing re-renders, and handed to `onChange` when the drag
 * ends. The column beside it (`flexible`) gives up the room, never below `keep`. Double-click puts the default back;
 * with the edge focused, the arrow keys move it.
 */
export function Resizer(props: {
  label: string;
  edge: "left" | "right"; // which side of its pane it's on
  width: number | null;
  min: number;
  max: number;
  keep?: number; // the least the flexible column keeps
  cssVar: string;
  host: () => HTMLElement | null;
  pane: () => HTMLElement | null;
  flexible: () => HTMLElement | null;
  onChange: (width: number | null) => void;
  onCollapse?: () => void; // dragged well past its minimum: collapse instead
}) {
  const [dragging, setDragging] = useState<null | "resize" | "collapse">(null);
  const handle = useRef<HTMLDivElement>(null);

  // React never sets the variable, so a re-render mid-drag can't undo it.
  useLayoutEffect(() => {
    const host = props.host();
    if (!host) return;
    if (props.width === null) host.style.removeProperty(props.cssVar);
    else host.style.setProperty(props.cssVar, `${props.width}px`);
  }, [props.width, props.cssVar]);

  const limits = () => {
    const current = props.pane()?.getBoundingClientRect().width ?? props.min;
    const room = (props.flexible()?.getBoundingClientRect().width ?? Infinity) - (props.keep ?? 360);
    return { current, max: Math.max(props.min, Math.min(props.max, current + Math.max(0, room))) };
  };
  const clamp = (w: number, max: number) => Math.round(Math.min(max, Math.max(props.min, w)));

  const start = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const host = props.host();
    if (!host) return;
    const { current, max } = limits();
    const x0 = e.clientX;
    let last = current;
    let collapsing = false;
    e.currentTarget.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    setDragging("resize");
    const move = (ev: PointerEvent) => {
      const dx = ev.clientX - x0;
      const raw = props.edge === "right" ? current + dx : current - dx;
      const now = Boolean(props.onCollapse) && raw < props.min - 60;
      if (now !== collapsing) setDragging(now ? "collapse" : "resize");
      collapsing = now;
      last = clamp(raw, max);
      host.style.setProperty(props.cssVar, `${last}px`);
    };
    const end = (ev: PointerEvent) => {
      handle.current?.releasePointerCapture(ev.pointerId);
      handle.current?.removeEventListener("pointermove", move);
      handle.current?.removeEventListener("pointerup", end);
      handle.current?.removeEventListener("pointercancel", end);
      document.body.classList.remove("resizing");
      setDragging(null);
      if (collapsing) {
        if (props.width === null) host.style.removeProperty(props.cssVar);
        else host.style.setProperty(props.cssVar, `${props.width}px`);
        props.onCollapse?.();
      } else if (last !== current) {
        props.onChange(last);
      }
    };
    handle.current?.addEventListener("pointermove", move);
    handle.current?.addEventListener("pointerup", end);
    handle.current?.addEventListener("pointercancel", end);
  };

  const key = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const { current, max } = limits();
    const toward = (e.key === "ArrowRight") === (props.edge === "right") ? 1 : -1; // the edge moves with the arrow
    props.onChange(clamp(current + toward * STEP * (e.shiftKey ? 4 : 1), max));
  };

  return (
    <div
      ref={handle}
      className={`resizer ${props.edge} ${dragging ?? ""}`}
      role="separator"
      aria-orientation="vertical"
      aria-label={props.label}
      aria-valuemin={props.min}
      aria-valuemax={props.max}
      aria-valuenow={props.width ?? undefined}
      tabIndex={0}
      title={`${props.label}. Double-click to reset.`}
      onPointerDown={start}
      onDoubleClick={() => props.onChange(null)}
      onKeyDown={key}
    />
  );
}

/** The rail's edge: drag it out, or double-click it, to open the sidebar again. */
export function RailEdge({ onExpand }: { onExpand: () => void }) {
  const start = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const x0 = e.clientX;
    const target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    document.body.classList.add("resizing");
    const end = (ev: PointerEvent) => {
      target.removeEventListener("pointerup", end);
      target.removeEventListener("pointercancel", end);
      document.body.classList.remove("resizing");
      if (ev.clientX - x0 > 40) onExpand();
    };
    target.addEventListener("pointerup", end);
    target.addEventListener("pointercancel", end);
  };
  return <div className="resizer right" role="separator" aria-orientation="vertical" aria-label="Open the sidebar" title="Drag out to open the sidebar" onPointerDown={start} onDoubleClick={onExpand} />;
}
