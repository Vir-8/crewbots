import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { Avatar, labelOf } from "../components/Avatar";
import { Icon } from "../components/Icon";
import { modelLabel } from "../components/ModelPicker";
import { Page, Segmented } from "../components/ui";
import type { Crew } from "../state";
import type { Budget, Plans, UsageSummary } from "../types";

// Each plan keeps one color everywhere on the page (orange and aqua, adjacent slots of the validated palette); the
// activity grid's magnitude is a separate one-hue blue ramp, so neither reads as one plan's. Both are set in styles.css.
const PLAN_COLOR: Record<string, string> = { claude: "var(--plan-claude)", codex: "var(--plan-codex)" };
const RAMP = [1, 2, 3, 4, 5].map((n) => `var(--heat-${n})`);
const PLAN_NAME: Record<string, string> = { prolite: "Pro Lite", plus: "Plus", pro: "Pro", team: "Team", business: "Business", enterprise: "Enterprise", edu: "Edu", free: "Free" };
const EFFORT_LABEL: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultra: "Ultra", default: "Default" };

/** What the crew has used, and what's left on your plans. */
export function Usage({ crew }: { crew: Crew }) {
  const [data, setData] = useState<UsageSummary | null>(null);
  const [plans, setPlans] = useState<Plans | null>(null);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const load = async (fresh = false) => {
    const [summary, p, b] = await Promise.allSettled([api.usageSummary(), api.plans(fresh), api.budgets()]);
    if (summary.status === "fulfilled") setData(summary.value);
    if (p.status === "fulfilled") setPlans(p.value);
    if (b.status === "fulfilled") setBudgets(b.value);
  };
  useEffect(() => {
    load();
    crew.loadModels();
  }, [crew.running.length]);

  return (
    <Page
      title="Usage"
      actions={
        <button
          className="icon-button"
          title="Ask the plans again"
          onClick={async () => {
            setRefreshing(true);
            await load(true);
            setRefreshing(false);
          }}
        >
          <Icon name="repeat" size={14} className={refreshing ? "spin" : ""} />
        </button>
      }
    >
      <section className="page-section">
        <h2>Your plans</h2>
        <div className="plan-grid">
          <PlanCard name="Claude" plan={plans?.claude} color={PLAN_COLOR.claude} />
          <PlanCard name="Codex" plan={plans?.codex} color={PLAN_COLOR.codex} />
        </div>
      </section>

      {data && (
        <>
          <section className="page-section">
            <h2>The crew's use</h2>
            <div className="stat-strip">
              <Stat value={compact(data.totals.claude + data.totals.codex)} label="Tokens this year" />
              <Stat value={data.peak ? compact(data.peak.tokens) : "–"} label="Peak day" title={data.peak ? long(data.peak.date) : undefined} />
              <Stat value={data.longest_turn ? duration(data.longest_turn.seconds) : "–"} label="Longest turn" title={data.longest_turn?.title} />
              <Stat value={`${data.streak.longest} ${data.streak.longest === 1 ? "day" : "days"}`} label="Longest streak" />
              <Stat value={`${data.streak.current} ${data.streak.current === 1 ? "day" : "days"}`} label="Current streak" />
            </div>
            <p className="faint small">Fresh tokens: input the model hadn't cached, plus output. It's what budgets count.</p>
          </section>

          <Activity data={data} account={plans?.codex.account?.daily ?? null} />

          <section className="page-section">
            <h2>Claude and Codex</h2>
            <Split totals={data.totals} />
          </section>

          <div className="usage-columns">
            <section className="page-section">
              <h2>Top models</h2>
              <Ranked rows={data.models.map((m) => ({ key: `${m.plan}-${m.model}`, label: modelLabel(crew, m.plan, m.model === "default" ? null : m.model, null), tag: m.plan === "codex" ? "Codex" : "Claude", color: PLAN_COLOR[m.plan], tokens: m.tokens }))} />
            </section>
            <section className="page-section">
              <h2>By bot</h2>
              <Ranked
                rows={data.bots.map((b) => (b.bot === "PR reviewer" ? { key: b.bot, label: b.bot, icon: "branch" as const, tokens: b.tokens } : { key: b.bot, label: labelOf(b.bot), face: b.bot, tokens: b.tokens }))}
              />
            </section>
          </div>

          {budgets.some((b) => b.tokens) && (
            <section className="page-section">
              <h2>Budgets</h2>
              <div className="list">
                {budgets
                  .filter((b) => b.tokens)
                  .map((b) => {
                    const share = b.used / b.tokens!;
                    return (
                      <div key={b.bot} className="list-row budget-row">
                        <Avatar name={b.bot} size={20} />
                        <span className="list-text">
                          <span className="list-title">{labelOf(b.bot)}</span>
                          <span className="faint small">
                            {compact(b.used)} of {compact(b.tokens!)} this {b.per}
                            {b.held.length > 0 && ` · ${b.held.length} held`}
                          </span>
                        </span>
                        <span className={`meter ${share >= 1 ? "limited" : share >= 0.8 ? "warning" : ""}`}>
                          <span style={{ width: `${Math.min(100, share * 100)}%` }} />
                        </span>
                      </div>
                    );
                  })}
              </div>
              <p className="faint small">Set a bot's budget on its role card. Over it, the bot's new turns wait until you release them.</p>
            </section>
          )}

          <section className="page-section">
            <h2>Insights</h2>
            <div className="stat-strip insights">
              <Stat value={data.efforts[0] ? (EFFORT_LABEL[data.efforts[0].effort] ?? data.efforts[0].effort) : "–"} label={data.efforts[0] ? `Most used reasoning, ${Math.round((data.efforts[0].turns / Math.max(1, data.turns)) * 100)}% of turns` : "Most used reasoning"} />
              <Stat value={data.turns ? compact((data.totals.claude + data.totals.codex) / data.turns) : "–"} label={`Tokens in an average turn, of ${data.turns.toLocaleString()}`} />
              <Stat value={data.counts.threads.toLocaleString()} label="Threads opened" />
              <Stat value={data.counts.reviews.toLocaleString()} label="Pull request reviews" />
              <Stat value={data.counts.routine_runs.toLocaleString()} label="Routine runs" />
              <Stat value={data.counts.room_messages.toLocaleString()} label="Group chat messages" />
            </div>
          </section>
        </>
      )}
      {!data && <p className="faint">Adding it up…</p>}
    </Page>
  );
}

function PlanCard({ name, plan, color }: { name: string; plan: Plans["claude"] | undefined; color: string }) {
  return (
    <div className="plan-card">
      <div className="plan-card-head">
        <span className="key" style={{ background: color }} />
        <span className="plan-card-name">{name}</span>
        {plan?.plan && plan.plan !== "subscription" && <span className="faint small">{PLAN_NAME[plan.plan] ?? plan.plan[0].toUpperCase() + plan.plan.slice(1)}</span>}
      </div>
      {!plan && <p className="faint small">Asking…</p>}
      {plan && !plan.available && <p className="error-text small">{plan.error}</p>}
      {plan?.available && plan.windows.length === 0 && <p className="faint small">No limits reported.</p>}
      {plan?.windows.map((w) => {
        const left = Math.max(0, 100 - w.used_percent);
        const tone = left <= 0 ? "limited" : left < 20 ? "warning" : "";
        return (
          <div key={w.name} className="plan-window">
            <div className="plan-window-top">
              <span>{w.name}</span>
              <span className="plan-left">
                <strong>{Math.round(left)}%</strong> left
              </span>
            </div>
            <div className={`meter ${tone}`}>
              <span style={{ width: `${Math.min(100, w.used_percent)}%`, background: tone ? undefined : color }} />
            </div>
            {w.resets_at && <div className="faint small">Resets {resets(w.resets_at)}</div>}
          </div>
        );
      })}
      {plan?.account?.lifetime_tokens != null && (
        <p className="faint small plan-account">
          All of Codex, not only the crew: {compact(plan.account.lifetime_tokens)} tokens
          {plan.account.peak_daily_tokens ? `, ${compact(plan.account.peak_daily_tokens)} on your busiest day` : ""}
          {plan.account.current_streak_days ? `, ${plan.account.current_streak_days}-day streak` : ""}.
        </p>
      )}
    </div>
  );
}

function Stat({ value, label, title }: { value: string; label: string; title?: string }) {
  return (
    <div className="stat" title={title}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

/** A year of days, a column a week, darker for quieter days. */
function Activity({ data, account }: { data: UsageSummary; account: { date: string; tokens: number }[] | null }) {
  const [source, setSource] = useState<"crew" | "account">("crew");
  const [plan, setPlan] = useState<"all" | "claude" | "codex">("all");
  const [hover, setHover] = useState<{ date: string; tokens: number; split?: string } | null>(null);
  const days = useMemo(() => {
    if (source === "account" && account) {
      const byDate = new Map(account.map((d) => [d.date, d.tokens]));
      return data.daily.map((d) => ({ date: d.date, tokens: byDate.get(d.date) ?? 0, split: undefined as string | undefined }));
    }
    return data.daily.map((d) => ({
      date: d.date,
      tokens: plan === "all" ? d.claude + d.codex : d[plan],
      split: plan === "all" && d.claude && d.codex ? `Claude ${compact(d.claude)} · Codex ${compact(d.codex)}` : undefined,
    }));
  }, [data, account, source, plan]);

  // A day's shade is its rank among the days with any use, so one huge day doesn't wash the rest out.
  const levels = useMemo(() => {
    const used = days.map((d) => d.tokens).filter((t) => t > 0).sort((a, b) => a - b);
    const rank = new Map<number, number>();
    used.forEach((t, i) => rank.has(t) || rank.set(t, i));
    return new Map(days.map((d) => [d.date, d.tokens > 0 ? Math.min(RAMP.length - 1, Math.floor(((rank.get(d.tokens) ?? 0) / used.length) * RAMP.length)) : -1]));
  }, [days]);
  // Weeks start on Sunday; pad the first week so each column is a real week.
  const pad = new Date(`${days[0].date}T12:00:00`).getDay();
  const cells = [...Array(pad).fill(null), ...days];
  const weeks: (typeof days[number] | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  const months = weeks.map((w, i) => {
    const first = w.find((d) => d)?.date;
    const prev = i > 0 ? weeks[i - 1].find((d) => d)?.date : undefined;
    return first && (!prev || first.slice(5, 7) !== prev.slice(5, 7)) ? new Date(`${first}T12:00:00`).toLocaleDateString(undefined, { month: "short" }) : "";
  });

  return (
    <section className="page-section">
      <div className="section-head">
        <h2>Token activity</h2>
        <span className="grow" />
        {source === "crew" && <Segmented value={plan} options={[["all", "All"], ["claude", "Claude"], ["codex", "Codex"]]} onChange={setPlan} />}
        {account && <Segmented value={source} options={[["crew", "Your crew"], ["account", "All of Codex"]]} onChange={setSource} />}
      </div>
      <div className="heatmap-wrap">
        <div className="heatmap" onMouseLeave={() => setHover(null)}>
          {weeks.map((week, i) => (
            <div key={i} className="heat-col">
              {week.map((d, j) =>
                d ? (
                  <span
                    key={d.date}
                    className={`heat-cell${hover?.date === d.date ? " on" : ""}`}
                    style={{ background: (levels.get(d.date) ?? -1) < 0 ? undefined : RAMP[levels.get(d.date)!] }}
                    aria-label={`${long(d.date)}: ${d.tokens.toLocaleString()} tokens`}
                    onMouseEnter={() => setHover(d)}
                  />
                ) : (
                  <span key={`pad-${j}`} className="heat-cell blank" />
                ),
              )}
              <span className="heat-month">{months[i]}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="heat-foot">
        <span className="faint small">
          {hover ? `${long(hover.date)}: ${hover.tokens ? `${hover.tokens.toLocaleString()} tokens${hover.split ? ` (${hover.split})` : ""}` : "nothing"}` : source === "account" ? "Everything you've run in Codex, not only the crew." : "Hover a day for its tokens."}
        </span>
        <span className="grow" />
        <span className="heat-legend faint small">
          Less
          <span className="heat-cell" />
          {RAMP.map((c) => (
            <span key={c} className="heat-cell" style={{ background: c }} />
          ))}
          More
        </span>
      </div>
    </section>
  );
}

function Split({ totals }: { totals: Record<string, number> }) {
  const all = Math.max(1, (totals.claude ?? 0) + (totals.codex ?? 0));
  return (
    <div className="split">
      <div className="split-bar">
        {(["claude", "codex"] as const).map((p) => (totals[p] ? <span key={p} style={{ width: `${(totals[p] / all) * 100}%`, background: PLAN_COLOR[p] }} /> : null))}
      </div>
      <div className="split-legend">
        {(["claude", "codex"] as const).map((p) => (
          <span key={p} className="legend-item">
            <span className="key" style={{ background: PLAN_COLOR[p] }} />
            {p === "claude" ? "Claude" : "Codex"} <strong>{compact(totals[p] ?? 0)}</strong>
            <span className="faint">{Math.round(((totals[p] ?? 0) / all) * 100)}%</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Ranked({ rows }: { rows: { key: string; label: string; tokens: number; tag?: string; color?: string; face?: string; icon?: "branch" }[] }) {
  const top = Math.max(1, ...rows.map((r) => r.tokens));
  if (rows.length === 0) return <p className="faint small">Nothing yet.</p>;
  return (
    <div className="ranked">
      {rows.map((r) => (
        <div key={r.key} className="ranked-row">
          <span className="ranked-label">
            {r.face ? <Avatar name={r.face} size={18} /> : r.icon ? <span className="ranked-icon"><Icon name={r.icon} size={12} /></span> : r.color ? <span className="key" style={{ background: r.color }} /> : null}
            <span className="ranked-name">{r.label}</span>
            {r.tag && <span className="faint small">{r.tag}</span>}
          </span>
          <span className="num">{compact(r.tokens)}</span>
          <span className="ranked-bar">
            <span style={{ width: `${(r.tokens / top) * 100}%`, background: r.color }} />
          </span>
        </div>
      ))}
    </div>
  );
}

function compact(n: number): string {
  if (n >= 1e9) return `${+(n / 1e9).toFixed(1)}bn`;
  if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${+(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K`;
  return String(Math.round(n));
}

function duration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m` : `${seconds}s`;
}

function long(day: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function resets(unix: number): string {
  const d = new Date(unix * 1000);
  const days = Math.round((new Date(d.toDateString()).getTime() - new Date(new Date().toDateString()).getTime()) / 86_400_000);
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return days === 0 ? `at ${time}` : days === 1 ? `tomorrow at ${time}` : `${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} at ${time}`;
}
