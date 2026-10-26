// What the app's modules expect of a browser, for tests that run in Node: a window (the bridge checks it for Tauri)
// and localStorage (drafts and pane widths).

const stored = new Map<string, string>();
Object.assign(globalThis, {
  window: globalThis,
  localStorage: {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => void stored.set(key, String(value)),
    removeItem: (key: string) => void stored.delete(key),
    clear: () => stored.clear(),
    key: (n: number) => [...stored.keys()][n] ?? null,
    get length() {
      return stored.size;
    },
  },
});
