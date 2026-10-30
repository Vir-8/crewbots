import { resets } from "../activityText";
import type { Plans } from "../types";

/** One plan's limits: how much of each is left, and when it resets, with whatever else its CLI says about it. */
export function PlanCard({ name, plan, color }: { name: string; plan: Plans["claude"] | undefined; color: string }) {
  return (
    <div className="plan-card">
      <div className="plan-card-head">
        <span className="key" style={{ background: color }} />
        <span className="plan-card-name">{name}</span>
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
      {plan?.notes.length ? <p className="faint small plan-notes">{plan.notes.join(" · ")}</p> : null}
    </div>
  );
}
