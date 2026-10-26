import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
const RAMP = [1, 2, 3, 4, 5];
const PLAN_NAME: Record<string, string> = { prolite: "Pro Lite", plus: "Plus", pro: "Pro", team: "Team", business: "Business", enterprise: "Enterprise", edu: "Edu", free: "Free" };
const EFFORT_LABEL: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultra: "Ultra", default: "Default" };
const STALE_S = 120; // the daemon refreshes a plan's answer once it's this old
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

type Range = "week" | "month" | "quarter" | "year";
const RANGES: { key: Range; label: string; days: number; span: string }[] = [
  { key: "week", label: "Week", days: 7, span: "the last 7 days" },
  { key: "month", label: "Month", days: 30, span: "the last 30 days" },
  { key: "quarter", label: "3 months", days: 91, span: "the last 3 months" },
  { key: "year", label: "Year", days: 365, span: "the last year" },
];

// Kept between visits, so coming back shows the last numbers at once while new ones load.
const kept = new Map<number, UsageSummary>();
let keptPlans: Plans | null = null;

function rememberedRange(): Range {
  try {
    const saved = localStorage.getItem("crewbots.analytics-range");
    return RANGES.some((r) => r.key === saved) ? (saved as Range) : "month";
  } catch {
    return "month";
  }
}

/** What the crew has used, and what's left on your plans. */
export function Analytics({ crew }: { crew: Crew }) {
  const [range, setRange] = useState<Range>(rememberedRange);
  const { days, span } = RANGES.find((r) => r.key === range)!;
  const [data, setData] = useState<UsageSummary | null>(kept.get(days) ?? null);
  const [plans, setPlans] = useState<Plans | null>(keptPlans);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [asking, setAsking] = useState(false);

  const choose = (next: Range) => {
    setRange(next);
    try {
      localStorage.setItem("crewbots.analytics-range", next);
    } catch {
      // not remembered, but still shown
    }
  };

  // The crew's own numbers are quick; until the new range's arrive, the last ones stay up.
  useEffect(() => {
    let live = true;
    if (kept.has(days)) setData(kept.get(days)!);
    api.usageSummary(days).then(
      (summary) => {
        kept.set(days, summary);
        if (live) setData(summary);
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, [days, crew.running.length]);

  // The plans take the CLIs seconds to answer, so the daemon keeps their last answer: show it, and if it's old, ask
  // again behind it.
  const askPlans = async (fresh: boolean) => {
    setAsking(true);
    try {
      let answer = await api.plans(fresh);
      keptPlans = answer;
      setPlans(answer);
      const now = Date.now() / 1000;
      if (!fresh && [answer.claude, answer.codex].some((p) => now - p.checked_at >= STALE_S)) {
        answer = await api.plans(true);
        keptPlans = answer;
        setPlans(answer);
      }
    } catch {
      // the cards keep what they had
    } finally {
      setAsking(false);
    }
  };
  useEffect(() => {
    askPlans(false);
    api.budgets().then(setBudgets, () => {});
    crew.loadModels();
  }, []);

  return (
    <Page
      title="Analytics"
      actions={
        <button className="icon-button" title="Ask the plans again" disabled={asking} onClick={() => askPlans(true)}>
          <Icon name="repeat" size={14} className={asking ? "spin" : ""} />
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

      <section className="page-section">
        <div className="section-head">
          <h2>The crew's use</h2>
          <span className="grow" />
          <Segmented value={range} options={RANGES.map((r) => [r.key, r.label] as [Range, string])} onChange={choose} />
        </div>
        {data ? (
          <>
            <div className="stat-strip">
              <Stat value={compact(data.totals.claude + data.totals.codex)} label="Tokens used" title={`In ${span}`} />
              <Stat value={data.peak ? compact(data.peak.tokens) : "–"} label={data.peak ? `Peak day, ${short(data.peak.date)}` : "Peak day"} />
              <Stat value={data.longest_turn ? duration(data.longest_turn.seconds) : "–"} label="Longest turn" title={data.longest_turn?.title} />
              <Stat value={plural(data.streak.longest, "day")} label="Longest streak" />
              <Stat value={plural(data.streak.current, "day")} label="Current streak" />
            </div>
            <p className="faint small">Fresh tokens: input the model hadn't cached, plus output. It's what budgets count.</p>
          </>
        ) : (
          <p className="faint">Adding it up…</p>
        )}
      </section>

      {data && (
        <>
          <Activity data={data} range={range} />

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
              <Ranked rows={data.bots.map((b) => (b.bot === "PR reviewer" ? { key: b.bot, label: b.bot, icon: "branch" as const, tokens: b.tokens } : { key: b.bot, label: labelOf(b.bot), face: b.bot, tokens: b.tokens }))} />
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

type Day = { date: string; tokens: number; split?: string };

/** The range's days, shaded by how much each used: a week as a row, a month as a calendar, and longer as a column a
 * week. */
function Activity({ data, range }: { data: UsageSummary; range: Range }) {
  const [plan, setPlan] = useState<"all" | "claude" | "codex">("all");
  const [hover, setHover] = useState<Day | null>(null);
  const days: Day[] = useMemo(
    () =>
      data.daily.map((d) => ({
        date: d.date,
        tokens: plan === "all" ? d.claude + d.codex : d[plan],
        split: plan === "all" && d.claude && d.codex ? `Claude ${compact(d.claude)} · Codex ${compact(d.codex)}` : undefined,
      })),
    [data, plan],
  );
  // A day's shade is its rank among the days with any use, so one huge day doesn't wash the rest out.
  const levels = useMemo(() => {
    const used = days.map((d) => d.tokens).filter((t) => t > 0).sort((a, b) => a - b);
    const rank = new Map<number, number>();
    used.forEach((t, i) => rank.has(t) || rank.set(t, i));
    return new Map(days.map((d) => [d.date, d.tokens > 0 ? 1 + Math.min(RAMP.length - 1, Math.floor(((rank.get(d.tokens) ?? 0) / used.length) * RAMP.length)) : 0]));
  }, [days]);
  const cell = (d: Day) => ({
    className: `${levels.get(d.date) ? `heat-${levels.get(d.date)}` : ""} ${hover?.date === d.date ? "on" : ""}`,
    "aria-label": `${long(d.date)}: ${d.tokens.toLocaleString()} tokens`,
    onMouseEnter: () => setHover(d),
  });

  return (
    <section className="page-section">
      <div className="section-head">
        <h2>Token activity</h2>
        <span className="grow" />
        <Segmented value={plan} options={[["all", "All"], ["claude", "Claude"], ["codex", "Codex"]]} onChange={setPlan} />
      </div>
      <div onMouseLeave={() => setHover(null)}>
        {range === "week" ? (
          <div className="heat-days">
            {days.map((d) => (
              <DayCell key={d.date} day={d} label={`${WEEKDAYS[dateOf(d.date).getDay()]} ${dateOf(d.date).getDate()}`} {...cell(d)} />
            ))}
          </div>
        ) : range === "month" ? (
          <Calendar days={days} cell={cell} />
        ) : (
          <Weeks days={days} cell={cell} max={range === "quarter" ? 44 : 18} />
        )}
      </div>
      <div className="heat-foot">
        <span className="faint small">
          {hover ? `${long(hover.date)}: ${hover.tokens ? `${hover.tokens.toLocaleString()} tokens${hover.split ? ` (${hover.split})` : ""}` : "nothing"}` : "Hover a day for its tokens."}
        </span>
        <span className="grow" />
        <span className="heat-legend faint small">
          Less
          <span className="heat-cell" />
          {RAMP.map((n) => (
            <span key={n} className={`heat-cell heat-${n}`} />
          ))}
          More
        </span>
      </div>
    </section>
  );
}

type CellProps = { className: string; "aria-label": string; onMouseEnter: () => void };

function DayCell({ day, label, className, ...rest }: { day: Day; label: string } & CellProps) {
  return (
    <span className={`day-cell ${className}`} {...rest}>
      <span className="day-label">{label}</span>
      {day.tokens > 0 && <span className="day-tokens">{compact(day.tokens)}</span>}
    </span>
  );
}

/** A month as a calendar: a row a week, under the days' names. */
function Calendar({ days, cell }: { days: Day[]; cell: (d: Day) => CellProps }) {
  const pad = dateOf(days[0].date).getDay();
  const slots: (Day | null)[] = [...Array(pad).fill(null), ...days];
  while (slots.length % 7) slots.push(null);
  return (
    <div className="heat-calendar">
      {WEEKDAYS.map((w) => (
        <span key={w} className="heat-weekday">
          {w}
        </span>
      ))}
      {slots.map((d, i) => {
        if (!d) return <span key={`blank-${i}`} />;
        const date = dateOf(d.date);
        const label = date.getDate() === 1 || d === days[0] ? date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : String(date.getDate());
        return <DayCell key={d.date} day={d} label={label} {...cell(d)} />;
      })}
    </div>
  );
}

/** Months to a year: a column a week, sized to fill the width up to `max` a square. */
function Weeks({ days, cell, max }: { days: Day[]; cell: (d: Day) => CellProps; max: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const watch = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  // Weeks start on Sunday; pad the first so each column is a real week.
  const slots: (Day | null)[] = [...Array(dateOf(days[0].date).getDay()).fill(null), ...days];
  const weeks: (Day | null)[][] = [];
  for (let i = 0; i < slots.length; i += 7) weeks.push(slots.slice(i, i + 7));
  const gap = max > 20 ? 4 : 3;
  const labels = 30;
  const size = width ? Math.max(8, Math.min(max, Math.floor((width - labels - (weeks.length - 1) * gap) / weeks.length))) : max;
  const starts = weeks.map((w, i) => {
    const first = w.find((d) => d)?.date;
    const before = i > 0 ? weeks[i - 1].find((d) => d)?.date : undefined;
    return first && (!before || first.slice(5, 7) !== before.slice(5, 7)) ? dateOf(first).toLocaleDateString(undefined, { month: "short" }) : "";
  });
  // A month that starts too close to the next one's label (the range's first, part-month week) goes without.
  const room = Math.ceil(28 / (size + gap));
  const months = starts.map((m, i) => (m && !starts.slice(i + 1, i + room).some(Boolean) ? m : ""));
  return (
    <div ref={box} className="heat-weeks" style={{ ["--cell" as string]: `${size}px`, ["--gap" as string]: `${gap}px` }}>
      <div className="heat-col heat-labels" style={{ width: labels - gap }}>
        <span className="heat-month" />
        {WEEKDAYS.map((w, i) => (
          <span key={w} className="heat-daylabel">
            {i % 2 ? w : ""}
          </span>
        ))}
      </div>
      {weeks.map((week, i) => (
        <div key={i} className="heat-col">
          <span className="heat-month">{months[i]}</span>
          {week.map((d, j) => (d ? <span key={d.date} {...cell(d)} className={`heat-cell ${cell(d).className}`} /> : <span key={`blank-${j}`} className="heat-cell blank" />))}
        </div>
      ))}
    </div>
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
            {r.face ? (
              <Avatar name={r.face} size={18} />
            ) : r.icon ? (
              <span className="ranked-icon">
                <Icon name={r.icon} size={12} />
              </span>
            ) : r.color ? (
              <span className="key" style={{ background: r.color }} />
            ) : null}
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

function dateOf(day: string): Date {
  return new Date(`${day}T12:00:00`);
}

function compact(n: number): string {
  if (n >= 1e9) return `${+(n / 1e9).toFixed(1)}bn`;
  if (n >= 1e6) return `${+(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${+(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K`;
  return String(Math.round(n));
}

function plural(n: number, word: string): string {
  return `${n} ${n === 1 ? word : `${word}s`}`;
}

function duration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m` : `${seconds}s`;
}

function short(day: string): string {
  return dateOf(day).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function long(day: string): string {
  return dateOf(day).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function resets(unix: number): string {
  const d = new Date(unix * 1000);
  const days = Math.round((new Date(d.toDateString()).getTime() - new Date(new Date().toDateString()).getTime()) / 86_400_000);
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return days === 0 ? `at ${time}` : days === 1 ? `tomorrow at ${time}` : `${d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })} at ${time}`;
}
