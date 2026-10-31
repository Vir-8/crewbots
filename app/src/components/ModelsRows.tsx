// The Models view's table rows: a team's, whose cells set every bot in it, and a bot's own. A cell the draft changes
// shows what it was, struck through, over the new value.

import type { ReactNode } from "react";
import type { ModelChange } from "../api-types";
import { type Draft, type Setup, givesUp, planned, setupOf } from "../crewModels";
import type { Crew } from "../state";
import type { Bot, Model } from "../types";
import { Avatar, labelOf } from "./Avatar";
import { BACKEND_LABEL, EFFORT_LABEL, EffortOptions, ModelOptions, backendFamily, blankEffort, chosenModel, defaultName } from "./ModelPicker";

const MIXED = "*"; // a team's select when its bots differ: no backend, model or effort is called that
const EACH_OWN = "Each bot's own";

type Family = keyof typeof BACKEND_LABEL;
type Change = (names: string[], change: ModelChange) => void;

export function TeamRow(props: { crew: Crew; team: string | null; bots: Bot[]; draft: Draft; picked: boolean; onPick: () => void; set: Change }) {
  const { crew, bots, draft } = props;
  const title = props.team ?? "No team";
  const names = bots.map((b) => b.name);
  const before = teamOf(crew, bots.map(setupOf)).words;
  const { family, model, effort, list, chosen, efforts, eachOwn, words: after } = teamOf(crew, bots.map((b) => planned(b, draft)));
  return (
    <div className="models-row team">
      <span className="models-name">
        <input type="checkbox" checked={props.picked} onChange={props.onPick} aria-label={`Select the bots in ${title}`} />
        <span>{title}</span>
        <span className="faint">{bots.length}</span>
      </span>
      <Cell was={before.backend !== after.backend ? before.backend : null}>
        <BackendSelect value={family} label={`${title} runs on`} onChange={(backend) => props.set(names, { backend })} />
      </Cell>
      <Cell was={before.model !== after.model ? before.model : null}>
        <select value={model} disabled={family === MIXED} title={family === MIXED ? "Its bots run on different CLIs: choose one first" : undefined} aria-label={`${title} model`} onChange={(e) => props.set(names, { model: e.target.value || null })}>
          {model === MIXED && <option value={MIXED} disabled>Mixed</option>}
          {family !== MIXED && <ModelOptions crew={crew} list={list} model={model === MIXED ? null : model || null} />}
        </select>
      </Cell>
      <Cell was={before.effort !== after.effort ? before.effort : null}>
        <select value={effort} disabled={crew.models !== null && efforts.length === 0 && (effort === "" || eachOwn)} title={eachOwn ? "Their models take different reasoning settings: set each bot's" : undefined} aria-label={`${title} reasoning`} onChange={(e) => props.set(names, { effort: e.target.value || null })}>
          {effort === MIXED && <option value={MIXED} disabled>Mixed</option>}
          {eachOwn ? <option value="">{EACH_OWN}</option> : <EffortOptions crew={crew} chosen={chosen} efforts={efforts} effort={effort === MIXED ? null : effort || null} />}
        </select>
      </Cell>
    </div>
  );
}

export function BotRow(props: { crew: Crew; bot: Bot; draft: Draft; picked: boolean; onPick: () => void; set: Change }) {
  const { crew, bot } = props;
  const was = setupOf(bot);
  const next = planned(bot, props.draft);
  const wasFamily = backendFamily(was.backend);
  const family = backendFamily(next.backend);
  const list = listOf(crew, family);
  const chosen = chosenModel(list, next.model);
  const efforts = chosen?.efforts ?? [];
  const wasModel = modelName(listOf(crew, wasFamily), was.model);
  const model = modelName(list, next.model);
  const wasEffort = effortName(crew, was);
  const effort = effortName(crew, next);
  const name = labelOf(bot.name);
  return (
    <div className="models-row bot">
      <span className="models-name">
        <input type="checkbox" checked={props.picked} onChange={props.onPick} aria-label={`Select ${name}`} />
        <Avatar name={bot.name} size={22} />
        <span className="models-bot">{name}</span>
        {next.team !== was.team && <s className="models-was" title="Its team now">{was.team ?? "No team"}</s>}
      </span>
      <Cell was={wasFamily !== family ? BACKEND_LABEL[wasFamily] : null} note={givesUp(bot, next)}>
        <BackendSelect value={family} label={`${name} runs on`} onChange={(backend) => props.set([bot.name], { backend })} />
      </Cell>
      <Cell was={wasModel !== model ? wasModel : null}>
        <select value={next.model ?? ""} aria-label={`${name} model`} onChange={(e) => props.set([bot.name], { model: e.target.value || null })}>
          <ModelOptions crew={crew} list={list} model={next.model} />
        </select>
      </Cell>
      <Cell was={wasEffort !== effort ? wasEffort : null}>
        <select value={next.effort ?? ""} disabled={crew.models !== null && efforts.length === 0 && !next.effort} aria-label={`${name} reasoning`} onChange={(e) => props.set([bot.name], { effort: e.target.value || null })}>
          <EffortOptions crew={crew} chosen={chosen} efforts={efforts} effort={next.effort} />
        </select>
      </Cell>
    </div>
  );
}

function Cell({ was, note, children }: { was: string | null; note?: string | null; children: ReactNode }) {
  return (
    <span className={`models-cell ${was === null ? "" : "changed"}`}>
      {was !== null && <s className="models-was">{was}</s>}
      {children}
      {note && <span className="models-note">{note}</span>}
    </span>
  );
}

/** What a team's selects show when its bots are set like this, and those choices in words. */
function teamOf(crew: Crew, setups: Setup[]) {
  const family = shared(setups.map((s) => backendFamily(s.backend)));
  const model = family === MIXED ? MIXED : shared(setups.map((s) => s.model ?? ""));
  const effort = shared(setups.map((s) => s.effort ?? ""));
  const list = family === MIXED ? [] : listOf(crew, family);
  const chosen = model === MIXED ? undefined : chosenModel(list, model || null);
  // Only the efforts every bot's model takes can be set for all of them.
  const offered = setups.map((s) => chosenModel(listOf(crew, backendFamily(s.backend)), s.model)?.efforts ?? []);
  const efforts = offered.reduce((a, b) => a.filter((e) => b.includes(e)));
  const eachOwn = efforts.length === 0 && offered.some((o) => o.length > 0);
  const words = {
    backend: label(family, (f) => BACKEND_LABEL[f]),
    model: label(shared(setups.map((s) => modelName(listOf(crew, backendFamily(s.backend)), s.model))), (m) => m),
    effort: label(effort, (e) => (e ? EFFORT_LABEL[e] ?? e : eachOwn ? EACH_OWN : blankEffort(crew, chosen, efforts))),
  };
  return { family, model, effort, list, chosen, efforts, eachOwn, words };
}

function BackendSelect({ value, label, onChange }: { value: string; label: string; onChange: (backend: Family) => void }) {
  return (
    <select value={value} aria-label={label} onChange={(e) => onChange(backendFamily(e.target.value))}>
      {value === MIXED && <option value={MIXED} disabled>Mixed</option>}
      {Object.entries(BACKEND_LABEL).map(([family, name]) => (
        <option key={family} value={family}>
          {name}
        </option>
      ))}
    </select>
  );
}

function listOf(crew: Crew, family: Family): Model[] {
  return crew.models?.[family] ?? [];
}

/** The value every one shares, or MIXED. */
function shared<T extends string>(values: T[]): T | typeof MIXED {
  return values.every((v) => v === values[0]) ? values[0] : MIXED;
}

function label<T extends string>(value: T | typeof MIXED, name: (value: T) => string): string {
  return value === MIXED ? "Mixed" : name(value);
}

function modelName(list: Model[], model: string | null): string {
  if (model) return list.find((m) => m.id === model)?.label ?? model;
  return defaultName(list) ? `Default (${defaultName(list)})` : "Default";
}

/** A bot's effort as its select names it. */
function effortName(crew: Crew, setup: Setup): string {
  if (setup.effort) return EFFORT_LABEL[setup.effort] ?? setup.effort;
  const chosen = chosenModel(listOf(crew, backendFamily(setup.backend)), setup.model);
  return blankEffort(crew, chosen, chosen?.efforts ?? []);
}
