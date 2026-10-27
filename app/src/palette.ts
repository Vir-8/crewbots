// The command palette's matching (⌘K): what you type against each item's title, as letters in order (fuzzy), with
// what you picked lately first. Kept apart from the component so it can be tested on its own.

export type Entry = { key: string; title: string; sub?: string; at?: string };

/** How well `query`'s letters, in order, match `text`: higher is better, -1 is no match. Letters together, at the
 * start of a word, and early in the text count for more. */
export function score(text: string, query: string): number {
  if (!query) return 0;
  const t = text.toLowerCase();
  const q = query.toLowerCase();
  let total = 0;
  let from = 0;
  let last = -2;
  for (const ch of q) {
    if (ch === " ") continue;
    const at = t.indexOf(ch, from);
    if (at < 0) return -1;
    const start = at === 0 || /[\s\-_/.#(]/.test(t[at - 1]);
    total += 1 + (at === last + 1 ? 5 : 0) + (start ? 8 : 0) - Math.min(at - from, 10) * 0.2;
    last = at;
    from = at + 1;
  }
  return total - t.length * 0.01;
}

/** The entries to show for `query`, best first. With nothing typed: those picked lately, then the most recent. */
export function rank<T extends Entry>(entries: T[], query: string, recent: string[], limit = 50): T[] {
  const lately = new Map(recent.map((key, i) => [key, recent.length - i]));
  const q = query.trim();
  if (!q) {
    return [...entries]
      .sort((a, b) => (lately.get(b.key) ?? 0) - (lately.get(a.key) ?? 0) || (b.at ?? "").localeCompare(a.at ?? ""))
      .slice(0, limit);
  }
  const scored: [T, number][] = [];
  for (const entry of entries) {
    const s = Math.max(score(entry.title, q), entry.sub ? score(entry.sub, q) - 3 : -1);
    if (s >= 0) scored.push([entry, s + (lately.get(entry.key) ?? 0) * 2]);
  }
  return scored.sort((a, b) => b[1] - a[1] || (b[0].at ?? "").localeCompare(a[0].at ?? "")).slice(0, limit).map(([e]) => e);
}

const RECENT = "crewbots.palette-recent";

/** What you picked from the palette lately, most recent last; kept in this browser profile. */
export function recent(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT) ?? "[]");
  } catch {
    return [];
  }
}

export function picked(key: string) {
  try {
    localStorage.setItem(RECENT, JSON.stringify([...recent().filter((k) => k !== key), key].slice(-20)));
  } catch {
    // not remembered, but still done
  }
}
