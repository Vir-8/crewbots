import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { face, labelOf } from "../components/Avatar";
import { Page, Segmented } from "../components/ui";
import type { Crew } from "../state";
import { limitText } from "../state";
import type { Budget, Usage as UsageData } from "../types";

const H = 200; // the plot's height
const BAR = 22; // a day's column; the rest of its slot is air
const GAP = 2; // between stacked bots, in the surface color
const PAD = 12; // above the plot, for the top tick's label

/** What the crew has used: the plans' limits, tokens by bot and day, and each bot against its budget. */
export function Usage({ crew }: { crew: Crew }) {
  const [days, setDays] = useState<"14" | "30">("14");
  const [data, setData] = useState<UsageData | null>(null);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [view, setView] = useState<"chart" | "table">("chart");
  useEffect(() => {
    api.usage(Number(days)).then(setData, () => setData(null));
    api.budgets().then(setBudgets, () => setBudgets([]));
  }, [days, crew.running.length]);

  const limits = crew.crewState?.limits ?? [];
  return (
    <Page title="Usage" actions={<Segmented value={days} options={[["14", "14 days"], ["30", "30 days"]]} onChange={setDays} />}>
      <section className="page-section">
        <h2>Plans</h2>
        {limits.length === 0 ? (
          <p className="faint">Each CLI reports where its plan's limits stand as the crew works; nothing yet since the daemon started.</p>
        ) : (
          <div className="limit-grid">
            {limits.map((l) => (
              <div key={`${l.plan}-${l.window}`} className={`limit-card ${l.status}`}>
                <div className="limit-head">
                  <span>{l.plan === "claude" ? "Claude" : "Codex"}</span>
                  <span className="faint">{l.window.replace(/_/g, " ")}</span>
                </div>
                <div className="limit-value">{l.used_percent != null ? `${Math.round(l.used_percent)}%` : l.status === "ok" ? "OK" : l.status}</div>
                <div className="meter">
                  <span style={{ width: `${Math.min(100, l.used_percent ?? (l.status === "limited" ? 100 : 0))}%` }} />
                </div>
                <div className="faint small">{limitText(l)}</div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="page-section">
        <div className="section-head">
          <h2>Tokens by day</h2>
          <span className="grow" />
          <Segmented value={view} options={[["chart", "Chart"], ["table", "Table"]]} onChange={setView} />
        </div>
        <p className="faint small">Fresh tokens: input the model hadn't cached, plus output. It's what budgets count.</p>
        {data ? view === "chart" ? <Chart data={data} /> : <DayTable data={data} /> : <p className="faint">Loading…</p>}
      </section>

      <section className="page-section">
        <h2>By bot</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th>Bot</th>
              <th className="num">Last {days} days</th>
              <th className="num">Turns</th>
              <th>Budget</th>
            </tr>
          </thead>
          <tbody>
            {(data?.bots ?? []).map((b) => {
              const budget = budgets.find((x) => x.bot === b.bot);
              return (
                <tr key={b.bot}>
                  <td>
                    <span className="key" style={{ background: face(b.bot).hex }} />
                    {labelOf(b.bot)}
                  </td>
                  <td className="num">{b.total.toLocaleString()}</td>
                  <td className="num">{b.turns.reduce((a, n) => a + n, 0).toLocaleString()}</td>
                  <td>
                    {budget?.tokens ? (
                      <span className="budget-cell">
                        <span className="meter">
                          <span style={{ width: `${Math.min(100, (budget.used / budget.tokens) * 100)}%` }} />
                        </span>
                        <span className="faint small">
                          {compact(budget.used)} of {compact(budget.tokens)} a {budget.per}
                        </span>
                      </span>
                    ) : (
                      <span className="faint">None</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </Page>
  );
}

function Chart({ data }: { data: UsageData }) {
  const [hover, setHover] = useState<number | null>(null);
  const bots = data.bots.filter((b) => b.total > 0);
  const totals = data.days.map((_, i) => bots.reduce((a, b) => a + b.by_day[i], 0));
  const top = niceMax(Math.max(1, ...totals));
  const ticks = [0, top / 2, top];
  const width = Math.max(560, data.days.length * 36);
  const slot = (width - 48) / data.days.length;
  const y = (v: number) => PAD + H - (v / top) * H;
  const every = Math.ceil(data.days.length / 7); // date labels, counted back from today so the last one is today

  const columns = useMemo(
    () =>
      data.days.map((_, i) => {
        let base = 0;
        const parts = bots
          .map((b) => ({ bot: b.bot, v: b.by_day[i] }))
          .filter((p) => p.v > 0)
          .map((p) => {
            const part = { ...p, from: base, to: base + p.v };
            base += p.v;
            return part;
          });
        return parts;
      }),
    [data],
  );

  if (bots.length === 0) return <p className="faint chart-empty">No turns in this time yet.</p>;
  return (
    <div className="chart">
      <div className="legend">
        {bots.map((b) => (
          <span key={b.bot} className="legend-item">
            <span className="key" style={{ background: face(b.bot).hex }} />
            {labelOf(b.bot)}
          </span>
        ))}
      </div>
      <div className="chart-scroll">
        <svg width={width} height={PAD + H + 28} role="img" aria-label="Fresh tokens per day, stacked by bot">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={40} x2={width} y1={y(t) + 0.5} y2={y(t) + 0.5} className="grid" />
              <text x={34} y={y(t) + 4} className="tick" textAnchor="end">
                {compact(t)}
              </text>
            </g>
          ))}
          {columns.map((parts, i) => {
            const x = 48 + i * slot + (slot - BAR) / 2;
            return (
              <g key={i} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={48 + i * slot} y={PAD} width={slot} height={H} fill="transparent" />
                {parts.map((p, j) => {
                  const top = y(p.to) + (j < parts.length - 1 ? GAP / 2 : 0);
                  const bottom = y(p.from) - (j > 0 ? GAP / 2 : 0);
                  const h = Math.max(0, bottom - top);
                  const last = j === parts.length - 1;
                  return <path key={p.bot} d={roundedTop(x, top, BAR, h, last ? Math.min(4, h) : 0)} fill={face(p.bot).hex} opacity={hover === null || hover === i ? 1 : 0.55} />;
                })}
                {(data.days.length - 1 - i) % every === 0 && (
                  <text x={x + BAR / 2} y={PAD + H + 18} className="tick" textAnchor="middle">
                    {short(data.days[i])}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {hover !== null && (
          <div className="chart-tip" style={{ left: Math.min(width - 190, 48 + hover * slot + slot) }}>
            <div className="tip-head">{long(data.days[hover])}</div>
            {[...columns[hover]].reverse().map((p) => (
              <div key={p.bot} className="tip-row">
                <span className="key" style={{ background: face(p.bot).hex }} />
                <span className="grow">{labelOf(p.bot)}</span>
                <span className="num">{p.v.toLocaleString()}</span>
              </div>
            ))}
            <div className="tip-row total">
              <span className="grow">Total</span>
              <span className="num">{totals[hover].toLocaleString()}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DayTable({ data }: { data: UsageData }) {
  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Day</th>
          {data.bots.map((b) => (
            <th key={b.bot} className="num">
              {labelOf(b.bot)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {[...data.days].reverse().map((d) => {
          const i = data.days.indexOf(d);
          return (
            <tr key={d}>
              <td>{long(d)}</td>
              {data.bots.map((b) => (
                <td key={b.bot} className="num">
                  {b.by_day[i] ? b.by_day[i].toLocaleString() : "–"}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** A column with its top corners rounded (the data end), square at the baseline. */
function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  if (h <= 0) return "";
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function niceMax(v: number): number {
  const step = 10 ** Math.floor(Math.log10(v));
  return Math.ceil(v / step) * step;
}

function compact(n: number): string {
  return n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${+(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K` : String(Math.round(n));
}

function short(day: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function long(day: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
