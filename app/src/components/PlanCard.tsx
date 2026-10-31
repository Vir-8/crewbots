import { resets } from "../activityText";
import type { Plans } from "../types";

// Each plan keeps one color everywhere (orange and aqua, adjacent slots of the validated palette), set in tokens.css.
export const PLAN_COLOR: Record<string, string> = { claude: "var(--plan-claude)", codex: "var(--plan-codex)", antigravity: "#4285f4" };

/** How much of a window is left, as a percent, and the meter's tone for it: a warning under a fifth, limited at none. */
export function leftOf(window: Plans["claude"]["windows"][number]): { left: number; tone: "" | "warning" | "limited" } {
  const left = Math.max(0, 100 - window.used_percent);
  return { left, tone: left <= 0 ? "limited" : left < 20 ? "warning" : "" };
}

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
        const { left, tone } = leftOf(w);
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
