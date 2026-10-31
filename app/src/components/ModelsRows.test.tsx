import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { type Draft, NO_CHANGES } from "../crewModels";
import type { Crew } from "../state";
import type { Bot, Models } from "../types";
import { BotRow, TeamRow } from "./ModelsRows";

const models: Models = {
  claude: [
    { id: "sonnet", label: "Sonnet", description: "", efforts: ["low", "medium", "high"], default_effort: null, default: true },
    { id: "haiku", label: "Haiku", description: "", efforts: [], default_effort: null, default: false },
  ],
  codex: [{ id: "gpt-6", label: "GPT-6", description: "", efforts: ["low", "high"], default_effort: "low", default: true }],
};
const crew = { models } as unknown as Crew;

function bot(name: string, fields: Partial<Bot>): Bot {
  return {
    name, title: name, description: "", backend: "claude", model: null, effort: null, access: "read_only", workspace: null, folder: `/tmp/${name}`, network: false,
    mcp: [], skills: [], chief: false, coordinator: false, path: null, display_name: null, avatar: null, context_window: null, team: "Engineers", ...fields,
  };
}

const was = (html: string) => [...html.matchAll(/<s class="models-was">([^<]*)<\/s>/g)].map((m) => m[1]);
const row = (b: Bot, draft: Draft) => renderToStaticMarkup(<BotRow crew={crew} bot={b} draft={draft} picked={false} onPick={() => {}} set={() => {}} />);

describe("the Models view's rows", () => {
  it("names a changed effort's old value as its select did", () => {
    const codex = bot("engineer", { backend: "codex" });
    expect(was(row(codex, { ...NO_CHANGES, changes: { engineer: { effort: "high" } } }))).toEqual(["Default effort (Low)"]);
    const haiku = bot("helper", { model: "haiku" });
    expect(was(row(haiku, { ...NO_CHANGES, changes: { helper: { model: "sonnet", effort: "high" } } }))).toEqual(["Haiku", "No reasoning setting"]);
  });

  it("names a team's old values as its selects did", () => {
    const team = (bots: Bot[], draft: Draft) => was(renderToStaticMarkup(<TeamRow crew={crew} team="Engineers" bots={bots} draft={draft} picked={false} onPick={() => {}} set={() => {}} />));
    const codex = [bot("a", { backend: "codex" }), bot("b", { backend: "codex" })];
    expect(team(codex, { ...NO_CHANGES, changes: { a: { effort: "high" }, b: { effort: "high" } } })).toEqual(["Default effort (Low)"]);
    const mixed = [bot("a", { model: "sonnet" }), bot("b", { backend: "codex" })];
    expect(team(mixed, { ...NO_CHANGES, changes: { b: { backend: "claude", model: null, effort: null } } })).toEqual(["Mixed"]);
  });

  it("can't set a team's reasoning when its models take none in common", () => {
    const bots = [bot("a", { model: "haiku" }), bot("b", { model: "sonnet", effort: "high" })];
    const html = renderToStaticMarkup(<TeamRow crew={crew} team="Engineers" bots={bots} draft={NO_CHANGES} picked={false} onPick={() => {}} set={() => {}} />);
    expect(html).toMatch(/<select[^>]*disabled=""[^>]*aria-label="Engineers reasoning"/);
  });
});
