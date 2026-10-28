import { useState } from "react";
import { api } from "../api";
import type { Block } from "./threadBlocks";

export function ReplaceMainCard({ block }: { block: Extract<Block, { kind: "replace" }> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [response, setResponse] = useState<{ ok: boolean; reason: string }>();
  const { data } = block;
  const result = block.result ?? response;
  const replace = async () => {
    setBusy(true);
    try {
      setResponse(await api.replaceMain(block.id));
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
      setBusy(false);
    }
  };
  const checks: { name: string; ok: boolean; detail: string }[] = data.checks ?? [];
  return <div className="ask push-card">
    <div className="ask-head">{result?.ok ? `Replaced ${data.target} in ${data.project}` : `Replace ${data.target ?? "main"} in ${data.project} with ${data.branch}?`}</div>
    <div className="permission-what">{data.summary}</div>
    {data.backup && <div className="small">Backup of the old tip: <code>{data.backup}</code></div>}
    {data.old_commits !== undefined && <div className="small">Commits since {data.upstream}: {data.old_commits} now, {data.new_commits} after</div>}
    {data.ci && <div className="small" title={(data.ci.steps ?? []).join("\n")}>{data.ci.note}{data.ci.steps?.length ? ` (${data.ci.steps.length} steps)` : ""}</div>}
    <pre className="push-commits">{checks.map((c) => `${c.ok ? "✓" : "✗"} ${c.name}: ${c.detail}`).join("\n")}</pre>
    {result?.ok && <div className="small">{result.reason}</div>}
    {block.started && !result && <div className={busy ? "small" : "push-error"}>{busy ? "Replacing…" : "Started; result unknown. Check the branch before making a new proposal."}</div>}
    {(data.error || result?.reason && !result.ok || error) && <pre className="push-error">{data.error || result?.reason || error}</pre>}
    {!result && !block.started && !data.error && <div className="permission-actions"><button className="button primary small" disabled={busy} onClick={replace}>Replace</button></div>}
  </div>;
}
