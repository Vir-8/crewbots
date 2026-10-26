import { usePlans } from "../plans";
import { Icon } from "./Icon";
import { PlanCard } from "./PlanCard";

/** /usage: what's left on each plan, over the message box, without sending anything. */
export function UsagePanel({ onClose }: { onClose: () => void }) {
  const { plans, asking, refresh } = usePlans();
  return (
    <div className="command-panel" onClick={(e) => e.stopPropagation()}>
      <div className="command-panel-head">
        <span className="command-panel-title">Usage</span>
        <span className="grow" />
        <button className="icon-button" title="Ask the plans again" disabled={asking} onClick={refresh}>
          <Icon name="repeat" size={13} className={asking ? "spin" : ""} />
        </button>
        <button className="text-button" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="plan-grid">
        <PlanCard name="Claude" plan={plans?.claude} color="var(--plan-claude)" />
        <PlanCard name="Codex" plan={plans?.codex} color="var(--plan-codex)" />
      </div>
    </div>
  );
}
