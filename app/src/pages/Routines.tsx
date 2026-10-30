import { useEffect, useState } from "react";
import { api, bridge, errorText } from "../api";
import { Avatar, labelOf } from "../components/Avatar";
import { ago } from "../components/bits";
import { Icon } from "../components/Icon";
import { Field, Page, Problem, Segmented, Sheet, Switch } from "../components/ui";
import type { Crew } from "../state";
import type { Routine, RoutineFile } from "../types";

const DAYS = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"];
const TRIGGERS: Record<string, string> = {
  "github.review_requested": "Someone asks you for a review",
  "github.ci_failed": "Checks fail on your pull request",
};

/** Work the crew starts on its own. Each routine is a file in routines/; this edits them for you. */
export function Routines({ crew, onOpen }: { crew: Crew; onOpen: (id: string) => void }) {
  const [editing, setEditing] = useState<string | null>(null); // a routine's name, or "" for a new one
  const routines = crew.routines?.routines ?? [];
  const starters = crew.routines?.starters ?? [];
  const errors = Object.entries(crew.routines?.errors ?? {});

  return (
    <Page
      title="Routines"
      actions={
        <button className="button small" onClick={() => setEditing("")}>
          <Icon name="plus" size={13} />
          New routine
        </button>
      }
    >
      <p className="page-lede">Work the crew starts on its own: on a schedule, as a message in that bot's chat, or when GitHub has something for you, as a thread.</p>
      {errors.map(([file, why]) => (
        <Problem key={file} what={`routines/${file} couldn't be read`} why={why} />
      ))}
      <div className="list">
        {routines.map((r) => (
          <RoutineRow key={r.name} routine={r} crew={crew} onEdit={() => setEditing(r.name)} onOpen={onOpen} />
        ))}
        {routines.length === 0 && <p className="faint list-empty">No routines yet. Add one, or a starter below.</p>}
      </div>
      {starters.length > 0 && (
        <section className="page-section">
          <h2>Starters</h2>
          <div className="list">
            {starters.map((s) => (
              <div key={s.name} className="list-row">
                <span className="list-text">
                  <span className="list-title">{s.name}</span>
                  <span className="faint">{s.description}</span>
                </span>
                <button className="button small" onClick={() => crew.addStarter(s.name)}>
                  <Icon name="plus" size={13} />
                  Add
                </button>
              </div>
            ))}
          </div>
        </section>
      )}
      {editing !== null && <RoutineEditor crew={crew} name={editing || null} onClose={() => setEditing(null)} />}
    </Page>
  );
}

function RoutineRow({ routine: r, crew, onEdit, onOpen }: { routine: Routine; crew: Crew; onEdit: () => void; onOpen: (id: string) => void }) {
  const toggle = (on: boolean) => crew.saveRoutine(r.name, { enabled: on }).catch(() => undefined);
  return (
    <div className={`list-row routine ${r.enabled ? "" : "off"}`} onClick={onEdit} role="button" tabIndex={0}>
      <Avatar name={r.bot} size={32} />
      <span className="list-text">
        <span className="list-title">{r.name}</span>
        <span className="faint">
          {r.on ? TRIGGERS[r.on] ?? r.on : describe(r.schedule ?? "")}
          {r.on && r.repo ? ` in ${r.repo}` : ""} · {labelOf(r.bot)}
          {r.enabled && r.next_run_at ? ` · next ${new Date(r.next_run_at).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}` : ""}
          {r.last_run_at && r.last_task_id && (
            <>
              {" · "}
              <a
                className="task-link"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpen(r.last_task_id!);
                }}
              >
                ran {ago(r.last_run_at)}
              </a>
            </>
          )}
        </span>
      </span>
      {r.schedule && r.enabled && (
        <button
          className="button small"
          onClick={async (e) => {
            e.stopPropagation();
            onOpen((await crew.runRoutine(r.name)).id);
          }}
        >
          <Icon name="play" size={12} />
          Run now
        </button>
      )}
      {r.proposed_by ? (
        <span className="routine-proposal" onClick={(e) => e.stopPropagation()}>
          <span className="faint small">Proposed by {labelOf(r.proposed_by)}</span>
          <button className="button small" onClick={() => crew.deleteRoutine(r.name)}>
            Decline
          </button>
          <button
            className="button small primary"
            onClick={async () => {
              const test = await crew.confirmRoutine(r.name);
              if (test) onOpen(test.id); // its first run, a test to watch
            }}
          >
            Turn on and test
          </button>
        </span>
      ) : (
        <Switch on={r.enabled} onChange={toggle} label={r.enabled ? "Turn off" : "Turn on"} />
      )}
    </div>
  );
}

/** "Weekdays at 9:00" for the schedules the editor makes; anything else as written. */
export function describe(cron: string): string {
  const [m, h, dom, mon, dow] = cron.trim().split(/\s+/);
  if (cron.trim() === "@hourly" || (m === "0" && h === "*" && dom === "*" && mon === "*" && dow === "*")) return "Every hour";
  if (!/^\d+$/.test(m ?? "") || !/^\d+$/.test(h ?? "") || dom !== "*" || mon !== "*") return cron;
  const time = new Date(2000, 0, 1, Number(h), Number(m)).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (dow === "*") return `Every day at ${time}`;
  if (dow === "1-5") return `Weekdays at ${time}`;
  if (/^[0-6]$/.test(dow)) return `${DAYS[Number(dow)]} at ${time}`;
  return cron;
}

type When = "daily" | "weekdays" | "weekly" | "custom";

function parse(cron: string): { when: When; time: string; day: number } {
  const [m, h, dom, mon, dow] = cron.trim().split(/\s+/);
  const simple = /^\d+$/.test(m ?? "") && /^\d+$/.test(h ?? "") && dom === "*" && mon === "*";
  const time = simple ? `${h.padStart(2, "0")}:${m.padStart(2, "0")}` : "09:00";
  if (simple && dow === "*") return { when: "daily", time, day: 1 };
  if (simple && dow === "1-5") return { when: "weekdays", time, day: 1 };
  if (simple && /^[0-6]$/.test(dow)) return { when: "weekly", time, day: Number(dow) };
  return { when: cron ? "custom" : "weekdays", time, day: 1 };
}

function cronOf(when: Exclude<When, "custom">, time: string, day: number): string {
  const [h, m] = time.split(":").map(Number);
  return `${m || 0} ${h || 0} * * ${when === "daily" ? "*" : when === "weekdays" ? "1-5" : day}`;
}

function RoutineEditor({ crew, name, onClose }: { crew: Crew; name: string | null; onClose: () => void }) {
  const creating = name === null;
  const [draft, setDraft] = useState<(Partial<RoutineFile> & { name: string }) | null>(
    creating ? { name: "", bot: crew.bots[0]?.name ?? "", schedule: "0 9 * * 1-5", goal: "", enabled: true } : null,
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [custom, setCustom] = useState(false); // its own choice: a simple cron under Custom stays there rather than snapping to Daily
  useEffect(() => {
    if (!creating) api.routineFile(name!).then((r) => {
      setDraft({ ...r, enabled: r.enabled ?? true });
      setCustom(parse(r.schedule ?? "").when === "custom");
    }, (e) => setError(errorText(e)));
  }, [name]);
  if (!draft) return <Sheet title="Edit routine" onClose={onClose} footer={null}>{error ? <p className="error-text">{error}</p> : <p className="faint">Loading…</p>}</Sheet>;

  const set = (fields: Partial<RoutineFile & { name: string }>) => setDraft((d) => (d ? { ...d, ...fields } : d));
  const kind = draft.on ? "trigger" : "schedule";
  const sched = parse(draft.schedule ?? "");
  const when = custom ? "custom" : sched.when;

  const save = async () => {
    setSaving(true);
    setError(null);
    const fields: Partial<RoutineFile> = {
      description: draft.description ?? "", bot: draft.bot, enabled: draft.enabled ?? true, goal: draft.goal,
      schedule: kind === "schedule" ? draft.schedule : "", on: kind === "trigger" ? draft.on : "",
      repo: kind === "trigger" ? draft.repo ?? "" : "", workspace: draft.workspace ?? "",
      budget: kind === "trigger" && draft.budget ? draft.budget : null,
    };
    try {
      if (creating) await api.newRoutine({ ...fields, name: draft.name });
      else await api.saveRoutine(name!, fields);
      await crew.refresh();
      onClose();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };
  const remove = () => crew.deleteRoutine(name!).then(onClose, () => undefined);

  return (
    <Sheet
      title={creating ? "New routine" : `Edit ${name}`}
      onClose={onClose}
      footer={
        <>
          {!creating && (
            <button className="button error-text" onClick={remove}>
              Delete
            </button>
          )}
          {error && <span className="error-text sheet-error">{error}</span>}
          <span className="grow" />
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : creating ? "Create routine" : "Save"}
          </button>
        </>
      }
    >
      <div className="field-row">
        {creating && (
          <Field label="Name">
            <input value={draft.name} onChange={(e) => set({ name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} placeholder="weekly-report" />
          </Field>
        )}
        <Field label="Bot">
          <select value={draft.bot ?? ""} onChange={(e) => set({ bot: e.target.value })}>
            {crew.bots.map((b) => (
              <option key={b.name} value={b.name}>
                {labelOf(b.name)}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <Field label="Description">
        <input value={draft.description ?? ""} onChange={(e) => set({ description: e.target.value })} placeholder="What it's for, in a line" />
      </Field>
      <Field label="When">
        <Segmented
          value={kind}
          options={[["schedule", "On a schedule"], ["trigger", "When GitHub has something"]]}
          onChange={(k) => {
            setCustom(false);
            set(k === "trigger" ? { on: "github.review_requested", schedule: "" } : { on: "", schedule: "0 9 * * 1-5" });
          }}
        />
      </Field>
      {kind === "schedule" ? (
        <div className="field-row">
          <Field label="Repeats">
            <select
              value={when}
              onChange={(e) => {
                const chosen = e.target.value as When;
                setCustom(chosen === "custom");
                if (chosen !== "custom") set({ schedule: cronOf(chosen, sched.time, sched.day) });
              }}
            >
              <option value="daily">Every day</option>
              <option value="weekdays">Weekdays</option>
              <option value="weekly">Once a week</option>
              <option value="custom">Custom (cron)</option>
            </select>
          </Field>
          {when === "weekly" && (
            <Field label="On">
              <select value={sched.day} onChange={(e) => set({ schedule: cronOf("weekly", sched.time, Number(e.target.value)) })}>
                {DAYS.map((d, i) => (
                  <option key={d} value={i}>
                    {d}
                  </option>
                ))}
              </select>
            </Field>
          )}
          {when === "custom" ? (
            <Field label="Cron" hint="minute hour day month weekday, in your time zone">
              <input className="mono" value={draft.schedule ?? ""} onChange={(e) => set({ schedule: e.target.value })} />
            </Field>
          ) : (
            <Field label="At">
              <input type="time" value={sched.time} onChange={(e) => set({ schedule: cronOf(when, e.target.value, sched.day) })} />
            </Field>
          )}
        </div>
      ) : (
        <>
          <div className="field-row">
            <Field label="Event">
              <select value={draft.on ?? ""} onChange={(e) => set({ on: e.target.value })}>
                {Object.entries(TRIGGERS).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Repo">
              <input value={draft.repo ?? ""} onChange={(e) => set({ repo: e.target.value })} placeholder="owner/name" />
            </Field>
          </div>
          <Field label="Workspace" hint="Your checkout of the repo, so the bot can read the code around the change.">
            <span className="workspace-field">
              <input value={draft.workspace ?? ""} onChange={(e) => set({ workspace: e.target.value })} placeholder="~/code/name" />
              <button className="button small" type="button" onClick={async () => { const p = await bridge.pickFolder(); if (p) set({ workspace: p }); }}>
                <Icon name="folder" size={13} />
                Choose
              </button>
            </span>
          </Field>
          <Field label="Budget" hint="Tokens for the thread it opens, with everything delegated from it. Blank: none.">
            <input type="number" min={1000} step={50000} value={draft.budget ?? ""} onChange={(e) => set({ budget: e.target.value ? Number(e.target.value) : null })} />
          </Field>
        </>
      )}
      <Field
        label="What it does"
        hint={kind === "schedule" ? <>{"{{activity}}"} is what the crew did since the last run; {"{{since}}"} is when that was.</> : <>{"{{pr.title}}"}, {"{{pr.url}}"}, {"{{pr.diff}}"} and {"{{pr.failures}}"} fill in the pull request.</>}
      >
        <textarea className="mono-area" rows={12} value={draft.goal ?? ""} onChange={(e) => set({ goal: e.target.value })} />
      </Field>
    </Sheet>
  );
}
