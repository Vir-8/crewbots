import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import type { Crew } from "../state";
import { limitText } from "../state";
import type { Bot, Budget, Routine } from "../types";
import { ago } from "./bits";
import { Icon } from "./Icon";

/** The crew as it's set up: who's on it and what each may do, what runs on its own, and what's wrong with any of it.
 * Role cards and routines are files; this shows them and opens them, and the daemon takes up edits by itself. */
export function CrewPage({ crew, onOpen }: { crew: Crew; onOpen: (id: string) => void }) {
  const [budgets, setBudgets] = useState<Map<string, Budget>>(new Map());
  useEffect(() => {
    const load = () => api.budgets().then((b) => setBudgets(new Map(b.map((x) => [x.bot, x]))), () => undefined);
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, [crew.running.length, crew.inbox.length]);

  const routines = crew.routines;
  const problems = [
    routines?.crew_error && { what: "A role card couldn't be read, so the crew carries on as it was", why: routines.crew_error },
    ...Object.entries(routines?.errors ?? {}).map(([file, why]) => ({ what: `routines/${file} couldn't be read`, why })),
    routines?.github_error && { what: "GitHub couldn't be reached for the triggers", why: routines.github_error },
  ].filter(Boolean) as { what: string; why: string }[];
  const limits = crew.crewState?.limits ?? [];

  return (
    <>
      <header className="topbar" data-tauri-drag-region>
        <span className="topbar-title" data-tauri-drag-region>
          Crew
        </span>
      </header>
      <div className="crew-page">
        <div className="crew-col">
          {problems.map((p) => (
            <div key={p.what} className="crew-problem">
              <Icon name="alert" size={14} />
              <div>
                <div>{p.what}</div>
                <div className="crew-problem-why">{p.why}</div>
              </div>
            </div>
          ))}

          <section>
            <h2>Bots</h2>
            <p className="faint crew-hint">Each bot is a role card. Edit one and the crew uses it from its next turn.</p>
            <div className="bot-cards">
              {crew.bots.map((b) => (
                <BotCard key={b.name} bot={b} budget={budgets.get(b.name)} working={crew.running.some((t) => t.bot === b.name)} onOpen={onOpen} />
              ))}
            </div>
          </section>

          {limits.length > 0 && (
            <section>
              <h2>Plans</h2>
              {limits.map((l) => (
                <div key={`${l.plan}-${l.window}`} className={`plan-row ${l.status}`}>
                  <span>{limitText(l)}</span>
                  {l.used_percent != null && <Meter used={l.used_percent} of={100} tone={l.status} />}
                </div>
              ))}
            </section>
          )}

          <section>
            <h2>Routines</h2>
            <p className="faint crew-hint">Work the crew starts on its own, on a schedule or when GitHub has something new.</p>
            {(routines?.routines ?? []).map((r) => (
              <RoutineRow key={r.name} routine={r} crew={crew} onOpen={onOpen} />
            ))}
            {(routines?.routines ?? []).length === 0 && <p className="faint">No routines yet.</p>}
            {(routines?.starters ?? []).length > 0 && (
              <div className="starters">
                <div className="side-label">Starter routines</div>
                {routines!.starters.map((s) => (
                  <div key={s.name} className="starter-row">
                    <div className="crew-row-text">
                      <span className="crew-row-name">{s.name}</span>
                      <span className="faint">{s.description}</span>
                    </div>
                    <button className="button small" onClick={() => crew.addStarter(s.name)}>
                      <Icon name="plus" size={13} />
                      Add
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </>
  );
}

const ACCESS: Record<string, string> = {
  read_only: "Reads only",
  workspace_write: "Writes in its task's folder",
  full: "Full access",
};

function BotCard({ bot, budget, working, onOpen }: { bot: Bot; budget?: Budget; working: boolean; onOpen: (id: string) => void }) {
  const writes = bot.access !== "read_only";
  const network = !writes ? null : bot.network === true ? "Any host" : Array.isArray(bot.network) && bot.network.length ? bot.network.join(", ") : "Off";
  return (
    <div className="bot-card">
      <div className="bot-card-head">
        <span className={`crew-dot ${working ? "on" : ""}`} />
        <span className="bot-card-name">{bot.name}</span>
        <span className="faint">{bot.title}</span>
        {bot.path && (
          <button className="icon-button bot-card-edit" title="Edit its role card" onClick={() => bridge.openPath(bot.path!)}>
            <Icon name="compose" size={14} />
          </button>
        )}
      </div>
      <div className="bot-card-desc">{bot.description}</div>
      <dl className="bot-facts">
        <dt>Runs on</dt>
        <dd>
          {bot.backend}
          {bot.model ? ` · ${bot.model}` : ""}
        </dd>
        <dt>Access</dt>
        <dd>{ACCESS[bot.access] ?? bot.access}{bot.can_delegate ? " · delegates" : ""}</dd>
        {network && (
          <>
            <dt>Network</dt>
            <dd>{network}</dd>
          </>
        )}
        <dt>MCP</dt>
        <dd>{bot.mcp.length ? bot.mcp.join(", ") : "Only the crew's tools"}</dd>
        {bot.skills.length > 0 && (
          <>
            <dt>Skills</dt>
            <dd>{bot.skills.join(", ")}</dd>
          </>
        )}
        {budget && (
          <>
            <dt>Budget</dt>
            <dd>
              {budget.tokens ? (
                <>
                  {budget.used.toLocaleString()} of {budget.tokens.toLocaleString()} tokens this {budget.per}
                  <Meter used={budget.used} of={budget.tokens} />
                </>
              ) : (
                `None · ${budget.used.toLocaleString()} tokens this week`
              )}
              {budget.held.map((id) => (
                <a key={id} className="task-link held-link" onClick={() => onOpen(id)}>
                  {id} is waiting
                </a>
              ))}
            </dd>
          </>
        )}
      </dl>
    </div>
  );
}

function RoutineRow({ routine: r, crew, onOpen }: { routine: Routine; crew: Crew; onOpen: (id: string) => void }) {
  const next = r.next_run_at ? new Date(r.next_run_at).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" }) : null;
  return (
    <div className={`routine-card ${r.enabled ? "" : "off"}`}>
      <div className="crew-row-text">
        <span className="crew-row-name">
          {r.name} <span className="faint">· {r.bot}</span>
        </span>
        {r.description && <span className="faint">{r.description}</span>}
        <span className="routine-when">
          {!r.enabled ? "Off" : r.on ? r.when.replace("on github.review_requested", "When you're asked for a review").replace("on github.ci_failed", "When checks fail on your pull requests") : `Next ${next}`}
          {r.last_run_at && r.last_task_id && (
            <>
              {" · "}
              <a className="task-link" onClick={() => onOpen(r.last_task_id!)}>
                last ran {ago(r.last_run_at)}
              </a>
            </>
          )}
          {r.budget ? ` · ${r.budget.toLocaleString()} tokens a run` : ""}
        </span>
      </div>
      <div className="routine-actions">
        {r.schedule && r.enabled && (
          <button className="button small" onClick={async () => onOpen((await crew.runRoutine(r.name)).id)}>
            <Icon name="play" size={12} />
            Run now
          </button>
        )}
        <button className="icon-button" title="Edit the routine" onClick={() => bridge.openPath(r.path)}>
          <Icon name="compose" size={14} />
        </button>
      </div>
    </div>
  );
}

function Meter({ used, of, tone }: { used: number; of: number; tone?: string }) {
  const share = Math.min(1, of > 0 ? used / of : 0);
  return (
    <span className={`meter ${tone ?? (share >= 1 ? "limited" : share >= 0.8 ? "warning" : "")}`}>
      <span style={{ width: `${share * 100}%` }} />
    </span>
  );
}
