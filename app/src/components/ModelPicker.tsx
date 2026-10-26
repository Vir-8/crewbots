import { useEffect } from "react";
import type { Crew } from "../state";
import type { Model } from "../types";

const EFFORT_LABEL: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultra: "Ultra" };

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
  const family = props.backend.startsWith("codex") ? "codex" : "claude";
  const list = crew.models?.[family] ?? [];
  const chosen = list.find((m) => m.id === props.model) ?? list.find((m) => m.default && !props.model);
  const efforts = chosen?.efforts ?? [];
  return (
    <span className="model-picker">
      <select value={props.model ?? ""} onChange={(e) => props.onChange(e.target.value || null, keep(props.effort, list.find((m) => m.id === e.target.value)))}>
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
        {props.model && !list.some((m) => m.id === props.model) && <option value={props.model}>{props.model}</option>}
      </select>
      <select value={props.effort ?? ""} onChange={(e) => props.onChange(props.model ?? null, e.target.value || null)} disabled={crew.models !== null && efforts.length === 0}>
        <option value="">{efforts.length === 0 && crew.models ? "No reasoning setting" : `Default effort${chosen?.default_effort ? ` (${EFFORT_LABEL[chosen.default_effort] ?? chosen.default_effort})` : ""}`}</option>
        {efforts.map((e) => (
          <option key={e} value={e}>
            {EFFORT_LABEL[e] ?? e}
          </option>
        ))}
        {props.effort && !efforts.includes(props.effort) && <option value={props.effort}>{EFFORT_LABEL[props.effort] ?? props.effort}</option>}
      </select>
    </span>
  );
}

/** "Sonnet · High": a model and effort, in words. */
export function modelLabel(crew: Crew, backend: string, model: string | null, effort: string | null): string {
  const family = backend.startsWith("codex") ? "codex" : "claude";
  const found = crew.models?.[family]?.find((m) => m.id === model) ?? (model ? null : crew.models?.[family]?.find((m) => m.default));
  const name = found?.id === "default" ? defaultName([found]) || "Claude" : found?.label ?? model ?? (family === "codex" ? "Codex" : "Claude");
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
function defaultName(list: Model[]): string {
  const found = list.find((m) => m.default);
  if (!found) return "";
  return found.id === "default" ? found.description.split(" · ")[0].replace(/\s*\[[^\]]*\]/g, "") : found.label;
}

function keep(effort: string | null | undefined, model: Model | undefined): string | null {
  return effort && model?.efforts.includes(effort) ? effort : null;
}
