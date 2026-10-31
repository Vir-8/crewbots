import { useState } from "react";
import { api, errorText } from "../api";
import type { Ci, Outcome } from "./cardEvents";
import type { Block } from "./threadBlocks";

/** A proposal card's one action: whether it's under way, what came of it (recorded, or the daemon's answer), and why
 * the call failed. */
export function useProposal(recorded: Outcome | undefined, call: () => Promise<Outcome>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<Outcome>();
  const act = async () => {
    setBusy(true);
    try {
      setResponse(await call());
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  };
  return { busy, error, result: recorded ?? response, act };
}

/** The CI a proposal's commit passed, its steps on hover. */
export function CiNote({ ci }: { ci: Ci }) {
  return <div className="small" title={(ci.steps ?? []).join("\n")}>{ci.note}{ci.steps?.length ? ` (${ci.steps.length} steps)` : ""}</div>;
}

export function PushCard({ block }: { block: Extract<Block, { kind: "push" }> }) {
  const { busy, error, result, act: push } = useProposal(block.result, () => api.push(block.id));
  const { data } = block;
  // A push recorded before `warning` was sent carries it only in its reason.
  const warning = result?.warning ?? (result?.reason !== "Push completed." ? result?.reason : undefined);
  const note = result?.ok && (result.pushed ? warning : result.reason);
  if (block.replaced && !result && !block.started) {
    return <div className="ask push-card replaced">
      <div className="ask-head">Push {data.project}</div>
      <div className="small">Replaced by a newer card below.</div>
    </div>;
  }
  return <div className="ask push-card">
    <div className="ask-head">{result?.ok && result.pushed ? `Pushed ${data.project}` : `Push ${data.project}?`}</div>
    <div className="permission-what">{data.summary}</div>
    {data.branch && <div className="small">Branch: <code>{data.branch}</code> · Upstream: <code>{data.upstream}</code>{data.push_url_display && <> · Destination: <code>{data.push_url_display}</code></>}</div>}
    {data.ci && <CiNote ci={data.ci} />}
    {data.commits && <pre className="push-commits">{data.commits.length ? data.commits.join("\n") : "Everything up-to-date. Nothing to push."}</pre>}
    {note && <div className="small">{note}</div>}
    {block.started && !result && <div className={busy ? "small" : "push-error"}>{busy ? "Pushing…" : "Push started; result unknown. Check the remote before making a new proposal."}</div>}
    {(data.error || result?.reason && !result.ok || error) && <pre className="push-error">{data.error || result?.reason || error}</pre>}
    {!result && !block.started && !data.error && <div className="permission-actions"><button className="button primary small" disabled={busy} onClick={push}>Push</button></div>}
  </div>;
}
