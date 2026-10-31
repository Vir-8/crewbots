import { Fragment, useEffect, useReducer, useState } from "react";
import { resets } from "../activityText";
import { api, bridge, errorText } from "../api";
import type { ModelChange, PresetBot, PresetsOut } from "../api-types";
import { Icon } from "../components/Icon";
import { BACKEND_LABEL } from "../components/ModelPicker";
import { BotRow, TeamRow } from "../components/ModelsRows";
import { PLAN_COLOR, leftOf } from "../components/PlanCard";
import { Problem } from "../components/ui";
import { NO_CHANGES, byTeam, draftReducer, planned, teamNames } from "../crewModels";
import { usePlans } from "../plans";
import type { Crew } from "../state";

/** The crew's backends, models and efforts in one table by team, with presets to switch many at once. What you change
 * is a draft until you apply it. */
export function CrewModels({ crew }: { crew: Crew }) {
  const [draft, dispatch] = useReducer(draftReducer, NO_CHANGES);
  const [presets, setPresets] = useState<PresetsOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const loadPresets = () => api.presets().then(setPresets, () => setPresets(null));
  useEffect(() => {
    crew.loadModels();
    loadPresets();
  }, []);

  const set = (names: string[], change: ModelChange) => dispatch({ kind: "set", bots: crew.bots, models: crew.models, names, change });
  const pick = (names: string[]) => setPicked((now) => (names.every((n) => now.includes(n)) ? now.filter((n) => !names.includes(n)) : [...new Set([...now, ...names])]));
  const count = Object.keys(draft.changes).length;

  const applyDraft = async () => {
    setApplying(true);
    setError(null);
    try {
      await api.editModels(draft.changes);
      await crew.refresh();
      dispatch({ kind: "discard" });
      loadPresets();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setApplying(false);
    }
  };
  const save = async (name: string) => {
    if (presets?.presets.some((p) => p.name === name) && !(await bridge.confirm(`Replace the preset “${name}” with the models shown?`, "Replace"))) return false;
    // What the table shows, so a draft can be kept as a preset without applying it.
    const bots = Object.fromEntries(crew.bots.map((b): [string, PresetBot] => {
      const { backend, model, effort } = planned(b, draft);
      return [b.name, { backend, model, effort }];
    }));
    try {
      setPresets(await api.savePreset(name, bots));
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    }
  };
  const remove = async (name: string) => {
    if (!(await bridge.confirm(`Delete the preset “${name}”? The bots keep the models they have.`, "Delete"))) return;
    try {
      setPresets(await api.deletePreset(name));
      if (draft.trying === name) dispatch({ kind: "discard" });
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <>
      <div className="models-bar">
        <div className="preset-row">
          <span className="faint">Preset</span>
          {presets?.presets.map((p) => {
            const inUse = presets.in_use.includes(p.name);
            return (
              <span key={p.name} className={`choice preset ${draft.trying === p.name ? "on" : ""} ${inUse ? "in-use" : ""}`}>
                <button onClick={() => dispatch({ kind: "try", bots: crew.bots, preset: p })} title={inUse ? "In use: the crew is set like this now" : `Try “${p.name}” in the table`}>
                  {inUse && <Icon name="check" size={12} />}
                  {p.name}
                </button>
                <button className="preset-delete" title={`Delete “${p.name}”`} onClick={() => remove(p.name)}>
                  <Icon name="x" size={11} />
                </button>
              </span>
            );
          })}
          <SavePreset onSave={save} />
        </div>
        <PlanMeters />
      </div>

      {count > 0 && (
        <div className="models-draft">
          <span className="grow">
            <strong>{draft.trying ? `Trying “${draft.trying}”:` : botsChange(count)}</strong>
            <span className="models-draft-detail">
              {draft.trying ? ` ${botsChange(count)}, shown below.` : ", shown below."} Nothing is saved until you apply.
              {draft.gone.length > 0 && ` It names ${draft.gone.join(", ")}, no longer in the crew, so ${draft.gone.length === 1 ? "that one is" : "those are"} skipped.`}
            </span>
          </span>
          <button className="button primary" onClick={applyDraft} disabled={applying}>
            {applying ? "Applying…" : "Apply"}
          </button>
          <button className="button" onClick={() => { setError(null); dispatch({ kind: "discard" }); }} disabled={applying}>
            Discard
          </button>
        </div>
      )}
      {error && <Problem what="Nothing was changed." why={error} />}
      {picked.length > 0 && <TeamBar crew={crew} picked={picked} onSet={(team) => { set(picked, { team }); setPicked([]); }} onClear={() => setPicked([])} />}

      <div className="models-table">
        <div className="models-row head">
          <span className="models-name">Team / bot</span>
          <span>Runs on</span>
          <span>Model</span>
          <span>Reasoning</span>
        </div>
        {byTeam(crew.bots, draft).map(({ team, bots }) => {
          const names = bots.map((b) => b.name);
          return (
            <Fragment key={team ?? ""}>
              <TeamRow crew={crew} team={team} bots={bots} draft={draft} picked={names.every((n) => picked.includes(n))} onPick={() => pick(names)} set={set} />
              {bots.map((b) => (
                <BotRow key={b.name} crew={crew} bot={b} draft={draft} picked={picked.includes(b.name)} onPick={() => pick([b.name])} set={set} />
              ))}
            </Fragment>
          );
        })}
      </div>
      <p className="faint small models-foot">A team's cell changes every bot in it, and shows Mixed when they differ. Each bot keeps its own settings, and a team only groups them.</p>
    </>
  );
}

const botsChange = (n: number) => (n === 1 ? "1 bot changes" : `${n} bots change`);

/** "Save as a preset…", which asks for the name in place. */
function SavePreset({ onSave }: { onSave: (name: string) => Promise<boolean> }) {
  const [name, setName] = useState<string | null>(null);
  if (name === null) {
    return (
      <button className="choice preset-new" onClick={() => setName("")}>
        Save as a preset…
      </button>
    );
  }
  const submit = async () => {
    if (name.trim() && (await onSave(name.trim()))) setName(null);
  };
  return (
    <form className="preset-name" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <input className="inline-input" autoFocus maxLength={40} value={name} placeholder="Codex is low" aria-label="Preset name" onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setName(null)} />
      <button className="button small primary" disabled={!name.trim()}>
        Save
      </button>
      <button type="button" className="button small" onClick={() => setName(null)}>
        Cancel
      </button>
    </form>
  );
}

/** Puts the bots ticked in the table in a team, or in none. */
function TeamBar({ crew, picked, onSet, onClear }: { crew: Crew; picked: string[]; onSet: (team: string | null) => void; onClear: () => void }) {
  const [team, setTeam] = useState("");
  return (
    <form className="models-picked" onSubmit={(e) => { e.preventDefault(); onSet(team.trim() || null); }}>
      <span>{picked.length === 1 ? "1 bot" : `${picked.length} bots`} selected</span>
      <input className="inline-input" list="models-teams" maxLength={40} value={team} placeholder="Team, or blank for none" aria-label="Team" onChange={(e) => setTeam(e.target.value)} />
      <datalist id="models-teams">
        {teamNames(crew.bots).map((t) => <option key={t} value={t} />)}
      </datalist>
      <button className="button small">Set team</button>
      <button type="button" className="button small" onClick={onClear}>
        Clear
      </button>
    </form>
  );
}

/** Each plan's tightest window: what's left of it, and when it resets. A plan whose limits aren't known isn't shown. */
function PlanMeters() {
  const { plans } = usePlans();
  if (!plans) return null;
  const shown = (["claude", "codex", "antigravity"] as const).flatMap((name) => {
    const plan = plans[name];
    if (!plan?.available || plan.windows.length === 0) return [];
    return [{ name, window: plan.windows.reduce((a, b) => (b.used_percent > a.used_percent ? b : a)) }];
  });
  return (
    <div className="plan-meters">
      {shown.map(({ name, window }) => {
        const { left, tone } = leftOf(window);
        return (
          <div key={name} className={`plan-meter ${tone}`}>
            <span className="plan-meter-name">
              {BACKEND_LABEL[name]} · {window.name}
            </span>
            <span className={`meter ${tone}`}>
              <span style={{ width: `${Math.min(100, window.used_percent)}%`, background: tone ? undefined : PLAN_COLOR[name] }} />
            </span>
            <span className="faint">
              {Math.round(left)}% left{window.resets_at ? ` · resets ${resets(window.resets_at)}` : ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}
