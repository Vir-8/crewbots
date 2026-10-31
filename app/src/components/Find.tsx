// ⌘F: the webview has no find of its own. The find bar, shared by a pull request's diff and the conversations, and
// marking what's found where text is drawn. Matches are counted from the messages themselves, not the page, so a
// message that isn't drawn yet (a long chat draws only what's in view) still counts and can be stepped to.

import { createContext, type ReactNode, type RefObject, useContext, useDeferredValue, useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import "../styles/find.css";

/** What's being looked for, for the text drawn under it to mark. */
export const Finding = createContext("");

/** Text with what's being looked for marked. */
export function Marked({ text }: { text: string }): ReactNode {
  const query = useContext(Finding);
  if (!query || !text) return text;
  const parts: ReactNode[] = [];
  const lower = text.toLowerCase();
  let from = 0;
  for (let at = lower.indexOf(query); at >= 0; at = lower.indexOf(query, at + query.length)) {
    parts.push(text.slice(from, at), <mark key={at} className="find-hit">{text.slice(at, at + query.length)}</mark>);
    from = at + query.length;
  }
  if (!from) return text;
  parts.push(text.slice(from));
  return parts;
}

/** How many times `query` (lowercased) is in `text`, as it's drawn. */
export function count(text: string, query: string): number {
  if (!query || !text) return 0;
  const lower = text.toLowerCase();
  let n = 0;
  for (let at = lower.indexOf(query); at >= 0; at = lower.indexOf(query, at + query.length)) n++;
  return n;
}

/** A [label](target) link, as `bits.tsx`'s Markdown draws it: the target in <...> when it has spaces, and with at most
 * one level of (...) in it otherwise. */
export const LINK = /\[([^\]\n]+)\]\((<[^>\n]+>|(?:[^()\s]|\([^()\s]*\))+)\)/g;

/** Markdown as it reads once drawn: without the marks that only format it (`bits.tsx`'s Markdown). */
export function shown(markdown: string): string {
  return markdown
    .split(/\r\n?|\n/)
    .filter((line) => !line.startsWith("```"))
    .map((line) => line.replace(/^#{1,6}\s+/, "").replace(/^\s*([-*+]|\d+[.)])\s+/, ""))
    .join("\n")
    .replace(/\[\[([^\]|#\n]+)(?:#[^\]|\n]*)?(?:\|([^\]\n]+))?\]\]/g, (_, page: string, label?: string) => (label ?? page).trim())
    .replace(LINK, "$1")
    .replace(/\*\*|`/g, "");
}

// Which find bar ⌘F opens: the one in the part of the window you last clicked in, the thread beside a chat or the
// chat itself.
let zone = "main";
const open = new Set<string>(); // the find bars there are
globalThis.document?.addEventListener("pointerdown", (e) => (zone = (e.target as Element).closest?.(".panel:not(.profile)") ? "panel" : "main"), true);

/** A find bar's state and keys: ⌘F opens it, ⌘G and ⇧⌘G step through what it found, Escape closes it. */
export function useFind(where = "main", onOpen?: () => void) {
  const [finding, setFinding] = useState(false);
  const [query, setQuery] = useState("");
  const [current, setCurrent] = useState(0);
  const [found, setFound] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const before = useRef<Element | null>(null); // what had the keyboard when find opened: it has it back after
  const lookFor = useDeferredValue(finding ? query.toLowerCase() : ""); // a big diff or chat marks behind your typing
  const step = (by: number) => found && setCurrent((c) => (c + by + found) % found);
  useEffect(() => {
    open.add(where);
    return () => void open.delete(where);
  }, [where]);
  useEffect(() => {
    const keys = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || (open.has(zone) ? zone : "main") !== where) return;
      if (e.key.toLowerCase() === "f") {
        e.preventDefault();
        onOpen?.();
        if (!finding) before.current = document.activeElement;
        setFinding(true);
        setTimeout(() => input.current?.select(), 0);
      } else if (e.key.toLowerCase() === "g" && finding) {
        e.preventDefault();
        step(e.shiftKey ? -1 : 1);
      }
    };
    window.addEventListener("keydown", keys);
    return () => window.removeEventListener("keydown", keys);
  }, [finding, found, where]);
  return {
    finding, query, current, found, lookFor, input, step,
    setFound: (n: number) => {
      setFound(n);
      setCurrent((c) => (c < n ? c : 0));
    },
    change: (q: string) => {
      setQuery(q);
      setCurrent(0);
    },
    close: () => {
      setFinding(false);
      (before.current as HTMLElement | null)?.focus?.();
    },
  };
}

export function FindBar({ find, placeholder, max }: { find: ReturnType<typeof useFind>; placeholder: string; max?: number }) {
  const { query, found, current, step } = find;
  return (
    <div className="find-bar" role="search">
      <Icon name="search" size={13} />
      <input
        ref={find.input as RefObject<HTMLInputElement>}
        autoFocus
        value={query}
        placeholder={placeholder}
        aria-label={placeholder}
        onChange={(e) => find.change(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") step(e.shiftKey ? -1 : 1);
          if (e.key === "Escape") find.close();
        }}
      />
      <span className="find-count faint">{query ? (found ? `${current + 1} of ${found}${max && found >= max ? "+" : ""}` : "No matches") : ""}</span>
      <button className="icon-button" onClick={() => step(-1)} disabled={!found} title="Previous (⇧↩)">
        <Icon name="chevronDown" size={13} className="flip" />
      </button>
      <button className="icon-button" onClick={() => step(1)} disabled={!found} title="Next (↩)">
        <Icon name="chevronDown" size={13} />
      </button>
      <button className="icon-button" onClick={find.close} title="Close (esc)">
        <Icon name="x" size={13} />
      </button>
    </div>
  );
}

/** Brings match `current` into view and marks it: `hits` says which drawn element holds it, and which of the marks
 * in it. */
export function useCurrentHit(box: RefObject<HTMLElement | null>, hits: { el: () => Element | null | undefined; nth: number }[], current: number, query: string) {
  useEffect(() => {
    const root = box.current;
    if (!root) return;
    root.querySelectorAll("mark.find-hit.current").forEach((m) => m.classList.remove("current"));
    const hit = hits[current];
    const el = hit?.el();
    if (!el) return;
    el.scrollIntoView({ block: "center" });
    const mark = el.querySelectorAll("mark.find-hit")[hit.nth];
    mark?.classList.add("current");
    mark?.scrollIntoView({ block: "center" });
  }, [current, hits, query]);
}
