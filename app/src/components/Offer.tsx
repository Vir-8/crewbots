import { createContext, useContext, useState } from "react";
import { api } from "../api";
import { labelOf } from "./Avatar";
import { BringIn } from "./BringIn";

/** What a chat can do for the chief's offer: open its card to customize it. */
export const Welcoming = createContext<{ customize: () => void } | null>(null);

/** The chief's offer under its greeting: bring in what Claude Code and Codex already know, customize it, or not now.
 * Once answered, it says how, and stays in the chat with the rest. */
export function Offer({ bot, outcome }: { bot: string; outcome?: Record<string, any> }) {
  const actions = useContext(Welcoming);
  const [bringing, setBringing] = useState(false);
  const [declining, setDeclining] = useState(false);
  const name = labelOf(bot);
  return (
    <div className="said offer">
      <div className="say">
        To get started, I can bring in what Claude Code and Codex already know: the folders you've worked in, as projects, and what they remember
        about you and your work.
      </div>
      {outcome ? (
        <div className="offer-outcome faint">{answered(outcome)}</div>
      ) : (
        <div className="offer-actions">
          <button className="button small primary" onClick={() => setBringing(true)}>
            Bring in context
          </button>
          {actions && (
            <button className="button small" onClick={actions.customize}>
              Customize {name}
            </button>
          )}
          <button className="button small" disabled={declining} onClick={() => { setDeclining(true); api.started().catch(() => setDeclining(false)); }}>
            Not now
          </button>
        </div>
      )}
      {bringing && <BringIn chief={name} onClose={() => setBringing(false)} onDone={() => setBringing(false)} />}
    </div>
  );
}

function answered(outcome: Record<string, any>): string {
  if (outcome.declined) return "Not now. You can bring them in any time, from Projects.";
  const projects: number = outcome.brought?.projects?.length ?? 0;
  const memories: number = outcome.brought?.memories ?? 0;
  const parts = [projects ? `${projects} ${projects === 1 ? "project" : "projects"}` : "", memories ? `${memories} ${memories === 1 ? "memory" : "memories"}` : ""].filter(Boolean);
  return parts.length ? `You brought in ${parts.join(" and ")}.` : "Started.";
}
