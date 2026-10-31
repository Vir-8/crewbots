// The Models view's draft: changes to the crew's backends, models, efforts and teams, made in its table or by trying a
// preset. Nothing is saved until Apply sends the draft in one call, which the daemon takes all or nothing.

import { api } from "./api";
import type { ModelChange, ModelsChanged, PresetBot, PresetOut } from "./api-types";
import { backendFamily, keptEffort } from "./components/ModelPicker";
import { ACCESS } from "./components/Profile";
import type { Bot, Models } from "./types";

/** What the Models view sets of a bot. */
export type Setup = { backend: string; model: string | null; effort: string | null; team: string | null };

/** Changes not applied yet: per bot, only what differs from the bot as it is. `trying` is the preset that filled it, and
 * `gone` the bots that preset names that aren't in the crew any more. */
export type Draft = { changes: Record<string, ModelChange>; trying: string | null; gone: string[] };

export type DraftAction =
  | { kind: "set"; bots: Bot[]; models: Models | null; names: string[]; change: ModelChange }
  | { kind: "try"; bots: Bot[]; preset: PresetOut }
  | { kind: "discard" };

export const NO_CHANGES: Draft = { changes: {}, trying: null, gone: [] };

export function setupOf(bot: Bot): Setup {
  return { backend: bot.backend, model: bot.model, effort: bot.effort, team: bot.team ?? null };
}

/** A bot as it will be once the draft is applied. */
export function planned(bot: Bot, draft: Draft): Setup {
  return { ...setupOf(bot), ...draft.changes[bot.name] };
}

export function draftReducer(draft: Draft, action: DraftAction): Draft {
  switch (action.kind) {
    case "set": {
      const changes = { ...draft.changes };
      for (const bot of action.bots.filter((b) => action.names.includes(b.name))) {
        changes[bot.name] = difference(bot, changed(bot, planned(bot, draft), action.change, action.models));
      }
      return { ...draft, changes: withoutEmpty(changes) };
    }
    case "try": {
      // The preset's differences, in place of whatever the draft had.
      const { preset, bots } = action;
      const changes: Record<string, ModelChange> = {};
      for (const bot of bots) {
        if (preset.bots[bot.name]) changes[bot.name] = difference(bot, { ...setupOf(bot), ...fromPreset(preset.bots[bot.name]) });
      }
      const names = new Set(bots.map((b) => b.name));
      return { changes: withoutEmpty(changes), trying: preset.name, gone: Object.keys(preset.bots).filter((name) => !names.has(name)) };
    }
    case "discard":
      return NO_CHANGES;
  }
}

/** Sends the draft: one call with only what changes. */
export function apply(draft: Draft): Promise<ModelsChanged> {
  return api.editModels(draft.changes);
}

/** The crew by team, as the draft would leave it: named teams in alphabetical order, then the bots in none. */
export function byTeam(bots: Bot[], draft: Draft): { team: string | null; bots: Bot[] }[] {
  const teams = new Map<string | null, Bot[]>();
  for (const bot of bots) {
    const team = planned(bot, draft).team;
    teams.set(team, [...(teams.get(team) ?? []), bot]);
  }
  return [...teams.entries()]
    .sort(([a], [b]) => (a === null ? 1 : b === null ? -1 : a.localeCompare(b)))
    .map(([team, members]) => ({ team, bots: members }));
}

/** The crew's team names, to offer when choosing one. */
export function teamNames(bots: Bot[]): string[] {
  return [...new Set(bots.flatMap((b) => (b.team ? [b.team] : [])))].sort((a, b) => a.localeCompare(b));
}

/** What a bot gives up on the CLI it's planned to run on, by the bot editor's rules, which the daemon applies too. */
export function givesUp(bot: Bot, setup: Setup): string | null {
  const family = backendFamily(setup.backend);
  if (family === "antigravity" && bot.access === "full") return `${ACCESS.full} becomes “${ACCESS.workspace_write}”`;
  if (family !== "claude" && Array.isArray(bot.network)) return "Its list of hosts becomes network off";
  return null;
}

/** A bot's setup after a change, by the bot editor's rules: a move to another CLI starts from that CLI's default model
 * and effort (and a move back to its own, from what it has), and a new model keeps the effort only if it offers it. */
function changed(bot: Bot, was: Setup, change: ModelChange, models: Models | null): Setup {
  let next = { ...was };
  if (change.backend !== undefined && backendFamily(change.backend) !== backendFamily(was.backend)) {
    next = backendFamily(change.backend) === backendFamily(bot.backend)
      ? { ...next, backend: bot.backend, model: bot.model, effort: bot.effort }
      : { ...next, backend: change.backend, model: null, effort: null };
  }
  if (change.model !== undefined) {
    const list = models?.[backendFamily(next.backend)] ?? [];
    next = { ...next, model: change.model, effort: keptEffort(list, change.model, next.effort) };
  }
  if (change.effort !== undefined) next.effort = change.effort;
  if (change.team !== undefined) next.team = change.team?.trim() || null;
  return next;
}

function fromPreset(setup: PresetBot): Omit<Setup, "team"> {
  return { backend: setup.backend ?? "claude", model: setup.model ?? null, effort: setup.effort ?? null };
}

function difference(bot: Bot, setup: Setup): ModelChange {
  const now = setupOf(bot);
  const change: ModelChange = {};
  if (setup.backend !== now.backend) change.backend = setup.backend;
  if (setup.model !== now.model) change.model = setup.model;
  if (setup.effort !== now.effort) change.effort = setup.effort;
  if (setup.team !== now.team) change.team = setup.team;
  return change;
}

function withoutEmpty(changes: Record<string, ModelChange>): Record<string, ModelChange> {
  return Object.fromEntries(Object.entries(changes).filter(([, change]) => Object.keys(change).length > 0));
}
