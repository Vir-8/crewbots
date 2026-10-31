// The crew's presets and its bulk edits of models, for the preview's bridge: kept while the preview is open. "Everyday"
// is the demo crew as it starts, and "Codex is low" moves its Codex bots to Claude.

import type { ModelChange, PresetBot, PresetOut } from "../../api-types";
import { backendFamily } from "../../components/ModelPicker";
import { bots } from "./make";

const EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultra"];
const ok = (data: unknown) => ({ ok: true, status: 200, data });
const refused = (status: number, detail: string) => ({ ok: false, status, data: { detail } });

const now = (): Record<string, PresetBot> => Object.fromEntries(bots.map((b) => [b.name, { backend: b.backend, model: b.model, effort: b.effort }]));
const presets: PresetOut[] = [
  { name: "Everyday", bots: now() },
  { name: "Codex is low", bots: { ...now(), engineer: { backend: "claude", model: "sonnet", effort: "high" }, critic: { backend: "claude", model: "sonnet", effort: null } } },
];

export function presetsRequest(method: string, pathname: string, parts: string[], body: any) {
  if (method === "PUT" && pathname === "/bots/models") return edit(body.bots, []);
  if (parts[0] !== "presets") return null;
  if (method === "GET" && parts.length === 1) return listing();
  const name = decodeURIComponent(parts[1] ?? "").trim();
  const at = presets.findIndex((p) => p.name === name);
  if (method === "PUT" && parts.length === 2) {
    if (!name || name.length > 40) return refused(422, "a preset's name is 1 to 40 characters");
    const saved: Record<string, PresetBot> = body?.bots ?? now();
    const unknown = Object.keys(saved).filter((bot) => !bots.some((b) => b.name === bot));
    if (unknown.length) return refused(422, `no bots named ${unknown.join(", ")}`);
    if (at < 0) presets.push({ name, bots: saved });
    else presets[at] = { name, bots: saved };
    return listing();
  }
  if (at < 0) return refused(404, `no preset named '${name}'`);
  if (method === "DELETE" && parts.length === 2) {
    presets.splice(at, 1);
    return listing();
  }
  if (method === "POST" && parts[2] === "apply") {
    const named = Object.entries(presets[at].bots);
    return edit(Object.fromEntries(named.filter(([bot]) => bots.some((b) => b.name === bot))), named.map(([bot]) => bot).filter((bot) => !bots.some((b) => b.name === bot)));
  }
  return null;
}

function listing() {
  const current = now();
  const inUse = presets.find((p) => {
    const present = Object.keys(p.bots).filter((bot) => bot in current);
    return present.length > 0 && present.every((bot) => same(p.bots[bot], current[bot]));
  });
  return ok({ presets: structuredClone(presets), in_use: inUse?.name ?? null });
}

const same = (a: PresetBot, b: PresetBot) => a.backend === b.backend && (a.model ?? null) === (b.model ?? null) && (a.effort ?? null) === (b.effort ?? null);

/** Every change checked before any is made, as the daemon does; a move to another CLI starts from its defaults. */
function edit(changes: Record<string, ModelChange>, skipped: string[]) {
  for (const [name, change] of Object.entries(changes)) {
    if (!bots.some((b) => b.name === name)) return refused(422, `no bot named ${name}`);
    if (change.effort && !EFFORTS.includes(change.effort)) return refused(422, `${name}.md: effort is one of ${EFFORTS.join(", ")}, not '${change.effort}'`);
  }
  const changed: string[] = [];
  for (const [name, change] of Object.entries(changes)) {
    const at = bots.findIndex((b) => b.name === name);
    const bot = bots[at];
    const moved = change.backend !== undefined && backendFamily(change.backend) !== backendFamily(bot.backend);
    const next = {
      ...bot,
      backend: change.backend ?? bot.backend,
      model: change.model !== undefined ? change.model : moved ? null : bot.model,
      effort: change.effort !== undefined ? change.effort : moved ? null : bot.effort,
      team: change.team !== undefined ? change.team?.trim() || null : bot.team,
    };
    if (next.backend === bot.backend && next.model === bot.model && next.effort === bot.effort && next.team === bot.team) continue;
    bots[at] = next;
    changed.push(name);
  }
  return ok({ changed, skipped });
}
