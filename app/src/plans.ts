import { useEffect, useState } from "react";
import { api } from "./api";
import type { Plans } from "./types";

const STALE_S = 120; // the daemon asks a CLI again once its answer is this old

// The plans' limits, kept while the app is open and shared by everything that shows them (Analytics, /usage).
let kept: Plans | null = null;
let asking: Promise<void> | null = null;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((listener) => listener());

/** Asks the daemon, which answers at once from what it last heard; if that's old, asks again for a fresh answer. */
export function askPlans(fresh = false): Promise<void> {
  if (asking && !fresh) return asking;
  asking = (async () => {
    try {
      kept = await api.plans(fresh);
      changed();
      const now = Date.now() / 1000;
      if (!fresh && [kept.claude, kept.codex].some((p) => now - p.checked_at >= STALE_S)) {
        kept = await api.plans(true);
        changed();
      }
    } catch {
      // whatever was shown stays
    } finally {
      asking = null;
      changed();
    }
  })();
  changed();
  return asking;
}

/** The plans, kept or asked for, and whether an ask is under way. */
export function usePlans(): { plans: Plans | null; asking: boolean; refresh: () => void } {
  const [, redraw] = useState(0);
  useEffect(() => {
    const listener = () => redraw((n) => n + 1);
    listeners.add(listener);
    askPlans();
    return () => {
      listeners.delete(listener);
    };
  }, []);
  return { plans: kept, asking: asking !== null, refresh: () => askPlans(true) };
}

/** When a limit resets, from now: "at 5:40 pm", "tomorrow at 9:00 am", "Wed, 28 Oct at 11:30 pm". */
export function resets(unix: number): string {
  const d = new Date(unix * 1000);
  const days = Math.round((new Date(d.toDateString()).getTime() - new Date(new Date().toDateString()).getTime()) / 86_400_000);
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return days === 0 ? `at ${time}` : days === 1 ? `tomorrow at ${time}` : `${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} at ${time}`;
}
