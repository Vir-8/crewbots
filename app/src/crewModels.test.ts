import { describe, expect, it } from "vitest";
import type { ModelChange } from "./api-types";
import { type Draft, type DraftAction, NO_CHANGES, byTeam, draftReducer, planned, setupOf } from "./crewModels";
import type { Bot, Models } from "./types";

const models: Models = {
  claude: [
    { id: "default", label: "Default (recommended)", description: "Opus", efforts: ["low", "medium", "high", "max"], default_effort: null, default: true },
    { id: "sonnet", label: "Sonnet", description: "", efforts: ["low", "medium", "high"], default_effort: null, default: false },
    { id: "haiku", label: "Haiku", description: "", efforts: [], default_effort: null, default: false },
  ],
  codex: [{ id: "gpt-6.1-sol", label: "GPT-6.1-Sol", description: "", efforts: ["low", "medium", "high", "xhigh"], default_effort: "low", default: true }],
};

function bot(name: string, fields: Partial<Bot>): Bot {
  return {
    name, title: name, description: "", backend: "claude", model: null, effort: null, access: "read_only", workspace: null, folder: `/Users/pat/Documents/Crewbots/${name}`, network: false,
    mcp: [], skills: [], chief: false, coordinator: false, path: null, display_name: null, avatar: null, context_window: null, team: null, ...fields,
  };
}

const crew = [
  bot("lead", { team: "Leads", model: "sonnet", effort: "high" }),
  bot("engineer-1", { team: "Engineers", backend: "codex", effort: "xhigh" }),
  bot("engineer-2", { team: "Engineers", backend: "codex" }),
  bot("engineer-3", { team: "Engineers", model: "sonnet" }),
  bot("helper", {}),
];

const run = (actions: DraftAction[], from: Draft = NO_CHANGES) => actions.reduce(draftReducer, from);
const set = (names: string[], change: ModelChange, list: Models | null = models): DraftAction => ({ kind: "set", bots: crew, models: list, names, change });
const team = (name: string, draft: Draft = NO_CHANGES) => byTeam(crew, draft).find((t) => t.team === name)!.bots.map((b) => b.name);

describe("the Models view's draft", () => {
  it("sets every bot in a team from the team's row, and only what differs is kept", () => {
    const draft = run([set(team("Engineers"), { backend: "claude" }), set(team("Engineers"), { model: "sonnet" })]);
    // A move to Claude starts from its defaults, so engineer-1's Codex effort goes; engineer-3 was already there.
    expect(draft.changes).toEqual({
      "engineer-1": { backend: "claude", model: "sonnet", effort: null },
      "engineer-2": { backend: "claude", model: "sonnet", effort: null },
    });
  });

  it("names the effort of a bot moved to another CLI, even one it had before, so it isn't reset", () => {
    const draft = run([set(["lead"], { backend: "codex" }), set(["lead"], { effort: "high" })]);
    expect(draft.changes).toEqual({ lead: { backend: "codex", model: null, effort: "high" } });
    const preset = { name: "Codex high", bots: { lead: { backend: "codex", model: null, effort: "high" } } };
    expect(run([{ kind: "try", bots: crew, preset }]).changes).toEqual(draft.changes);
  });

  it("fills only a preset's differences, and says which of its bots are gone", () => {
    const preset = {
      name: "Codex is low",
      bots: {
        lead: { backend: "claude", model: "sonnet", effort: "high" },
        "engineer-1": { backend: "claude", model: "sonnet", effort: "high" },
        "engineer-9": { backend: "claude", model: null, effort: null },
      },
    };
    const draft = run([set(["helper"], { team: "Helpers" }), { kind: "try", bots: crew, preset }]);
    expect(draft).toEqual({ changes: { "engineer-1": { backend: "claude", model: "sonnet", effort: "high" } }, trying: "Codex is low", gone: ["engineer-9"] });
  });

  it("clears an effort the new model doesn't offer, and keeps one it does", () => {
    expect(run([set(["lead"], { model: "haiku" })]).changes).toEqual({ lead: { model: "haiku", effort: null } });
    expect(run([set(["lead"], { model: null })]).changes).toEqual({ lead: { model: null } }); // the default takes high
    expect(run([set(["lead"], { model: "haiku" }, null)]).changes).toEqual({ lead: { model: "haiku" } }); // models unknown
  });

  it("gives a bot moved back to its own CLI what it had", () => {
    expect(run([set(["engineer-1"], { backend: "claude" }), set(["engineer-1"], { backend: "codex" })]).changes).toEqual({});
  });

  it("shows a bot under its new team, and the bots in none last", () => {
    const draft = run([set(["engineer-3"], { team: " Leads " })]);
    expect(byTeam(crew, draft).map((t) => [t.team, t.bots.map((b) => b.name)])).toEqual([
      ["Engineers", ["engineer-1", "engineer-2"]],
      ["Leads", ["lead", "engineer-3"]],
      [null, ["helper"]],
    ]);
  });

  it("goes back to the crew as it is on discard", () => {
    const draft = run([set(team("Engineers"), { backend: "claude" }), { kind: "discard" }]);
    expect(crew.map((b) => planned(b, draft))).toEqual(crew.map(setupOf));
    expect(draft.trying).toBeNull();
  });
});
