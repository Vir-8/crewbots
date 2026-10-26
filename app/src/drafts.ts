// Unsent text, kept per message box in localStorage, so reloading or relaunching the app loses nothing.

import { useEffect, useState } from "react";

const PREFIX = "crewbots.draft.";
const MAX_CHARS = 500_000; // a bigger draft isn't kept: localStorage holds about 5MB for the whole app

export function readDraft<T>(key: string | undefined): T | null {
  if (!key) return null;
  try {
    const saved = localStorage.getItem(PREFIX + key);
    return saved ? (JSON.parse(saved) as T) : null;
  } catch {
    return null;
  }
}

/** Keeps `value` under `key`, or forgets it when there's nothing in it. */
export function writeDraft(key: string | undefined, value: unknown, empty: boolean): void {
  if (!key) return;
  try {
    const text = JSON.stringify(value);
    if (empty || text.length > MAX_CHARS) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, text);
  } catch {
    // not kept, but still typed
  }
}

/** A text field's value that survives a reload: `key` names the field, e.g. a pull request's review summary. The
 * third value forgets it at once, for when it's sent just as the field goes away. */
export function useDraft(key: string | undefined): [string, (value: string) => void, () => void] {
  const [value, setValue] = useState(() => readDraft<string>(key) ?? "");
  useEffect(() => writeDraft(key, value, !value.trim()), [key, value]);
  return [value, setValue, () => writeDraft(key, "", true)];
}
