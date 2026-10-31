import { useEffect } from "react";
import type { Crew } from "../state";
import type { Model } from "../types";

export const EFFORT_LABEL: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultra: "Ultra", default: "Default" };
export const backendFamily = (backend: string) => backend === "antigravity" ? "antigravity" : backend.startsWith("codex") ? "codex" : "claude";
export const BACKEND_LABEL = { claude: "Claude", codex: "Codex", antigravity: "Antigravity" };

/** The model a backend should use, and its reasoning effort, from what that CLI says it offers. Blank means the
 * CLI's own default, for either. */
export function ModelPicker(props: {
  crew: Crew;
  backend: string;
  model: string | null | undefined;
  effort: string | null | undefined;
  onChange: (model: string | null, effort: string | null) => void;
}) {
  const { crew } = props;
  useEffect(() => crew.loadModels(), [crew.loadModels]);
  const list = crew.models?.[backendFamily(props.backend)] ?? [];
  const chosen = chosenModel(list, props.model);
  return (
    <span className="model-picker">
      <select value={props.model ?? ""} onChange={(e) => props.onChange(e.target.value || null, keptEffort(list, e.target.value || null, props.effort ?? null))}>
        <ModelOptions crew={crew} list={list} model={props.model} />
      </select>
      <select value={props.effort ?? ""} onChange={(e) => props.onChange(props.model ?? null, e.target.value || null)} disabled={crew.models !== null && !chosen?.efforts.length}>
        <EffortOptions crew={crew} chosen={chosen} efforts={chosen?.efforts ?? []} effort={props.effort} />
      </select>
    </span>
  );
}

/** A model select's choices: the CLI's default, then what it lists, and the one set if the CLI doesn't list it. */
export function ModelOptions({ crew, list, model }: { crew: Crew; list: Model[]; model: string | null | undefined }) {
  return (
    <>
      <option value="">{crew.models ? `Default${defaultName(list) ? ` (${defaultName(list)})` : ""}` : "Loading models…"}</option>
      {groups(list).map(([title, models]) =>
        title ? (
          <optgroup key={title} label={title}>
            {models.map((m) => (
              <option key={m.id} value={m.id} title={m.description}>
                {m.label}
              </option>
            ))}
          </optgroup>
        ) : (
          models.map((m) => (
            <option key={m.id} value={m.id} title={m.description}>
              {m.label}
            </option>
          ))
        ),
      )}
      {model && !list.some((m) => m.id === model) && <option value={model}>{model}</option>}
    </>
  );
}

/** An effort select's choices: the model's default, the `efforts` it can take, and the one set if it isn't among them. */
export function EffortOptions(props: { crew: Crew; chosen: Model | undefined; efforts: string[]; effort: string | null | undefined }) {
  const { crew, chosen, efforts, effort } = props;
  return (
    <>
      <option value="">{efforts.length === 0 && crew.models ? "No reasoning setting" : `Default effort${chosen?.default_effort ? ` (${EFFORT_LABEL[chosen.default_effort] ?? chosen.default_effort})` : ""}`}</option>
      {efforts.map((e) => (
        <option key={e} value={e}>
          {EFFORT_LABEL[e] ?? e}
        </option>
      ))}
      {effort && !efforts.includes(effort) && <option value={effort}>{EFFORT_LABEL[effort] ?? effort}</option>}
    </>
  );
}

/** The model a bot runs on: the one it names, or the CLI's default when it names none. */
export function chosenModel(list: Model[], model: string | null | undefined): Model | undefined {
  return list.find((m) => m.id === model) ?? list.find((m) => m.default && !model);
}

/** The effort a bot keeps on moving to `model`: only one that model offers. With nothing listed it can't be told, so
 * it stays, as the daemon keeps it. */
export function keptEffort(list: Model[], model: string | null, effort: string | null): string | null {
  if (!effort || list.length === 0) return effort;
  return chosenModel(list, model)?.efforts.includes(effort) ? effort : null;
}

/** "Sonnet · High": a model and effort, in words. */
export function modelLabel(crew: Crew, backend: string, model: string | null, effort: string | null): string {
  const family = backendFamily(backend);
  const found = crew.models?.[family]?.find((m) => m.id === model) ?? (model ? null : crew.models?.[family]?.find((m) => m.default));
  const name = found?.id === "default" ? defaultName([found]) || BACKEND_LABEL[family] : found?.label ?? model ?? BACKEND_LABEL[family];
  return effort ? `${name} · ${EFFORT_LABEL[effort] ?? effort}` : name;
}

/** Claude's list has names that follow the newest version ("opus") and specific versions ("claude-opus-4-8"):
 * grouped, so you can tell a model that moves with releases from one that stays put. */
function groups(list: Model[]): [string, Model[]][] {
  const models = list.filter((m) => m.id !== "default");
  const pinned = models.filter((m) => m.id.startsWith("claude-"));
  if (pinned.length === 0 || pinned.length === models.length) return [["", models]];
  return [
    ["Latest of each", models.filter((m) => !pinned.includes(m))],
    ["Specific versions", pinned],
  ];
}

/** What the CLI's default model is: Codex names it; Claude describes it ("Opus 4.6 with 1M context · …"). */
export function defaultName(list: Model[]): string {
  const found = list.find((m) => m.default);
  if (!found) return "";
  return found.id === "default" ? found.description.split(" · ")[0].replace(/\s*\[[^\]]*\]/g, "") : found.label;
}
