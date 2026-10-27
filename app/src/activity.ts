// What the bots hand each other: work they delegate, reviews they ask for, and questions, as chains, for the
// Activity page.

import type { Task } from "./types";
import { ACTIVE } from "./types";

export type Verb = "delegated" | "asked" | "review";

/** One hand-off: a task a bot gave another, and how deep in its chain it is (0 for the first). */
export type Step = { task: Task; from: string; verb: Verb; depth: number };

/** The work bots handed on from one task (a chat, a thread or a group chat's seat): each hand-off from it, with
 * everything handed on below that. */
export type Chain = { steps: Step[]; from: Task | undefined; busy: boolean; updated_at: string };

/** Whether a bot handed this task to another, rather than the user starting it or a bot opening it for itself. Code
 * review's own reviewers aren't the crew's bots: what they pass each other shows in Code review, not here. */
export function handedOff(task: Task): boolean {
  return (
    Boolean(task.parent_id) &&
    task.kind !== "review" &&
    !["user", "crewbots", task.bot].includes(task.created_by) &&
    !task.created_by.startsWith("routine")
  );
}

export function verbOf(task: Task): Verb {
  return task.kind === "ask" ? "asked" : task.review_of ? "review" : "delegated";
}

/** Still going: running, waiting on a turn, held, or waiting on an answer. */
export function going(task: Task): boolean {
  return ACTIVE.includes(task.status) || task.status === "held" || task.status === "needs_input";
}

/** Every chain of hand-offs, by the task they were handed on from, the one with the latest change first. A chain is
 * busy while any step in it is going. */
export function chains(tasks: Map<string, Task>): Chain[] {
  const children = new Map<string, Task[]>();
  for (const t of tasks.values()) if (t.parent_id) children.set(t.parent_id, [...(children.get(t.parent_id) ?? []), t]);
  const underHandOff = (t: Task): boolean => {
    for (let up = t.parent_id ? tasks.get(t.parent_id) : undefined; up; up = up.parent_id ? tasks.get(up.parent_id) : undefined) {
      if (handedOff(up)) return true;
    }
    return false;
  };

  const roots = [...tasks.values()].filter((t) => handedOff(t) && !underHandOff(t)).sort((a, b) => a.created_at.localeCompare(b.created_at));
  const byFrom = new Map<string, Step[]>();
  for (const root of roots) {
    const steps = byFrom.get(root.parent_id!) ?? [];
    byFrom.set(root.parent_id!, steps);
    // Down through everything below it, the bots' own threads included, keeping the hand-offs in order.
    const walk = (t: Task, depth: number) => {
      const handed = handedOff(t);
      if (handed) steps.push({ task: t, from: t.created_by, verb: verbOf(t), depth });
      const below = [...(children.get(t.id) ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at));
      for (const child of below) walk(child, handed ? depth + 1 : depth);
    };
    walk(root, 0);
  }
  return [...byFrom]
    .map(([from, steps]) => ({
      steps,
      from: tasks.get(from),
      busy: steps.some((s) => going(s.task)),
      updated_at: steps.reduce((latest, s) => (s.task.updated_at > latest ? s.task.updated_at : latest), ""),
    }))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

/** Who handed work to whom since `since`, and how often, most first. */
export function pairs(tasks: Map<string, Task>, since: string): { from: string; to: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const t of tasks.values()) {
    if (handedOff(t) && t.created_at >= since) counts.set(`${t.created_by}\u0000${t.bot}`, (counts.get(`${t.created_by}\u0000${t.bot}`) ?? 0) + 1);
  }
  return [...counts]
    .map(([key, count]) => {
      const [from, to] = key.split("\u0000");
      return { from, to, count };
    })
    .sort((a, b) => b.count - a.count || a.from.localeCompare(b.from) || a.to.localeCompare(b.to));
}
