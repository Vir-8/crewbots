import { useEffect, useState } from "react";
import { api } from "../api";
import { resets, usePlans } from "../plans";
import type { TaskStatus } from "../types";
import { labelOf } from "./Avatar";
import { Icon } from "./Icon";

const EFFORT: Record<string, string> = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max", ultra: "Ultra" };

/** /status: this chat's bot, model and session, how full its context is, and what's left on its plan. */
export function StatusPanel({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const [status, setStatus] = useState<TaskStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { plans } = usePlans();
  useEffect(() => {
    api.taskStatus(taskId).then(setStatus, (e) => setError(String(e).replace(/^Error: /, "")));
  }, [taskId]);
  const plan = status?.plan === "codex" ? plans?.codex : status?.plan === "claude" ? plans?.claude : undefined;
  const used = status?.context_percent ?? null;

  return (
    <div className="command-panel" onClick={(e) => e.stopPropagation()}>
      <div className="command-panel-head">
        <span className="command-panel-title">Status</span>
        <span className="grow" />
        <button className="text-button" onClick={onClose}>
          Close
        </button>
      </div>
      {error && <p className="error-text small">{error}</p>}
      {!status && !error && <p className="faint small">Asking…</p>}
      {status && (
        <dl className="status-facts">
          <dt>Bot</dt>
          <dd>
            {labelOf(status.bot)} <span className="faint">@{status.bot}</span>
          </dd>
          <dt>Model</dt>
          <dd>
            {status.backend ?? "?"} · {status.model ?? "default model"} · {status.effort ? EFFORT[status.effort] ?? status.effort : "default effort"}
          </dd>
          <dt>Session</dt>
          <dd>
            {status.session_id ? <span className="mono">{status.session_id}</span> : <span className="faint">None yet: it starts with the first message</span>}
            {status.session_id && <span className="faint"> · {status.open ? "open" : "resumes on the next message"}</span>}
          </dd>
          <dt>Context</dt>
          <dd>
            {used === null ? (
              <span className="faint">Not measured yet: it is after each turn</span>
            ) : (
              <span className="status-meter">
                <span className={`meter ${status.rollover_percent && used >= status.rollover_percent * 0.85 ? "warning" : ""}`}>
                  <span style={{ width: `${Math.min(100, used)}%` }} />
                </span>
                <span>
                  <strong>{Math.round(100 - used)}%</strong> left
                </span>
                {status.rollover_percent && <span className="faint">· a fresh session from a briefing at {status.rollover_percent}%</span>}
              </span>
            )}
          </dd>
          {plan?.windows.map((w) => (
            <span key={w.name} className="status-limit">
              <dt>{w.name}</dt>
              <dd>
                <span className="status-meter">
                  <span className={`meter ${w.used_percent >= 100 ? "limited" : w.used_percent >= 80 ? "warning" : ""}`}>
                    <span style={{ width: `${Math.min(100, w.used_percent)}%`, background: w.used_percent >= 80 ? undefined : `var(--plan-${status.plan})` }} />
                  </span>
                  <span>
                    <strong>{Math.round(Math.max(0, 100 - w.used_percent))}%</strong> left
                  </span>
                  {w.resets_at && <span className="faint">· resets {resets(w.resets_at)}</span>}
                </span>
              </dd>
            </span>
          ))}
          <dt>Turns</dt>
          <dd>{status.turns}</dd>
        </dl>
      )}
      {status && !status.open && status.session_id && (
        <p className="faint small status-note">
          <Icon name="alert" size={12} /> Its session is closed while idle; the context figure is from its last turn.
        </p>
      )}
    </div>
  );
}
