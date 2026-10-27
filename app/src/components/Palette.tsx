import { useEffect, useMemo, useRef, useState } from "react";
import { picked, rank, recent } from "../palette";
import { Icon, type IconName } from "./Icon";
import "../styles/palette.css";

export type Item = { key: string; title: string; sub?: string; at?: string; icon: IconName; group: string; run: () => void };

/** ⌘K: jump to any chat, bot, group, thread, pull request or page, or run a command. Typing narrows it; ↑↓ pick,
 * ↩ goes, Escape closes. */
export function Palette({ items, onClose }: { items: Item[]; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [pick, setPick] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => rank(items, query, recent()), [items, query]);
  const go = (item: Item | undefined) => {
    if (!item) return;
    picked(item.key);
    onClose();
    item.run();
  };
  useEffect(() => setPick(0), [query]);
  useEffect(() => {
    list.current?.querySelector(".palette-item.on")?.scrollIntoView({ block: "nearest" }); // a promise, in newer engines
  }, [pick]);
  return (
    <div className="palette-backdrop" onClick={onClose}>
      <div className="palette" role="dialog" aria-label="Go to or run" onClick={(e) => e.stopPropagation()}>
        <div className="palette-search">
          <Icon name="search" size={14} />
          <input
            autoFocus
            value={query}
            placeholder="Go to a chat, thread, pull request or page, or run a command"
            aria-label="Go to or run"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                const n = shown.length || 1;
                setPick((pick + (e.key === "ArrowDown" ? 1 : n - 1)) % n);
              } else if (e.key === "Enter") {
                e.preventDefault();
                go(shown[pick]);
              } else if (e.key === "Escape") {
                e.preventDefault();
                onClose();
              }
            }}
          />
          <kbd className="faint small">esc</kbd>
        </div>
        <div className="palette-list" ref={list} role="listbox">
          {shown.length === 0 && <div className="palette-empty faint">Nothing matches.</div>}
          {shown.map((item, i) => (
            <button
              key={item.key}
              role="option"
              aria-selected={i === pick}
              className={`palette-item ${i === pick ? "on" : ""}`}
              onMouseMove={() => i !== pick && setPick(i)}
              onClick={() => go(item)}
            >
              <Icon name={item.icon} size={14} />
              <span className="palette-title">{item.title}</span>
              {item.sub && <span className="palette-sub faint">{item.sub}</span>}
              <span className="grow" />
              <span className="palette-group faint small">{item.group}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
