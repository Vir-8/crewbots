import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

// What's at the top of the view while something is drawn above it, and where: put back there once it's drawn, so what
// you're reading stays put. WebKit doesn't keep your place on its own and Chromium does, so it's done here, the same
// in both, with the browser's own turned off where this scrolls (else both would move it).
let anchor: { root: HTMLElement; el: Element; top: number } | null = null;

/** Draws what's in it once it comes within a couple of screens of the view, and until then only keeps its room:
 * a long diff opens as fast as a short one. Drawn, it stays drawn. */
export function Later({ height, now, className, children }: { height: number; now?: boolean; className?: string; children: () => ReactNode }) {
  const [near, setNear] = useState(false);
  const drawn = now || near;
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (drawn || !el) return;
    const root = scroller(el);
    if (root) root.style.overflowAnchor = "none";
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        if (root && !anchor) anchor = atTop(root);
        setNear(true);
      },
      { root, rootMargin: "1600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [drawn]);
  useLayoutEffect(() => {
    if (!near || !anchor) return;
    const { root, el, top } = anchor;
    root.scrollTop += el.getBoundingClientRect().top - top;
    anchor = { root, el, top: el.getBoundingClientRect().top }; // another drawn in the same go adds nothing more
    queueMicrotask(() => (anchor = null));
  }, [near]);
  return (
    <div ref={box} className={className} style={drawn ? undefined : { height }}>
      {drawn ? children() : null}
    </div>
  );
}

function atTop(root: HTMLElement): { root: HTMLElement; el: Element; top: number } | null {
  const box = root.getBoundingClientRect();
  const el = document.elementFromPoint(box.left + box.width / 2, box.top + 8);
  return el && root.contains(el) ? { root, el, top: el.getBoundingClientRect().top } : null;
}

/** What scrolls it up and down: not a box that only scrolls sideways (a diff's), which counts as scrolling both ways. */
function scroller(el: HTMLElement): HTMLElement | null {
  for (let at = el.parentElement; at; at = at.parentElement) {
    const { overflowY } = getComputedStyle(at);
    if ((overflowY === "auto" || overflowY === "scroll") && at.scrollHeight > at.clientHeight) return at;
  }
  return null;
}
