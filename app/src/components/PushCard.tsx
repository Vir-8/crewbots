import { useState } from "react";
import { api } from "../api";
import type { Block } from "./threadBlocks";

export function PushCard({ block }: { block: Extract<Block, { kind: "push" }> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<{ ok: boolean; reason: string; pushed?: boolean }>();
  const { data } = block;
  const result = block.result ?? response;
  const push = async () => {
    setBusy(true);
    try {
      setResponse(await api.push(block.id));
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
      setBusy(false);
    }
  };
  return <div className="ask push-card">
    <div className="ask-head">{result?.ok && result.pushed ? `Pushed ${data.project}` : `Push ${data.project}?`}</div>
    <div className="permission-what">{data.summary}</div>
    {data.branch && <div className="small">Branch: <code>{data.branch}</code> · Upstream: <code>{data.upstream}</code>{data.push_url_display && <> · Destination: <code>{data.push_url_display}</code></>}</div>}
    {data.ci && <div className="small" title={(data.ci.steps ?? []).join("\n")}>{data.ci.note}{data.ci.steps?.length ? ` (${data.ci.steps.length} steps)` : ""}</div>}
    {data.commits && <pre className="push-commits">{data.commits.length ? data.commits.join("\n") : "Everything up-to-date. Nothing to push."}</pre>}
    {result?.ok && result.reason !== "Push completed." && <div className="small">{result.reason}</div>}
    {block.started && !result && <div className={busy ? "small" : "push-error"}>{busy ? "Pushing…" : "Push started; result unknown. Check the remote before making a new proposal."}</div>}
    {(data.error || result?.reason && !result.ok || error) && <pre className="push-error">{data.error || result?.reason || error}</pre>}
    {!result && !block.started && !data.error && <div className="permission-actions"><button className="button primary small" disabled={busy} onClick={push}>Push</button></div>}
  </div>;
}
