import { api } from "../api";
import { CiNote, useProposal } from "./PushCard";
import type { Block } from "./threadBlocks";

export function ReplaceMainCard({ block }: { block: Extract<Block, { kind: "replace" }> }) {
  const { busy, error, result, act: replace } = useProposal(block.result, () => api.replaceMain(block.id));
  const { data } = block;
  const checks = data.checks ?? [];
  return <div className="ask push-card">
    <div className="ask-head">{result?.ok ? `Replaced ${data.target} in ${data.project}` : `Replace ${data.target ?? "main"} in ${data.project} with ${data.branch}?`}</div>
    <div className="permission-what">{data.summary}</div>
    {data.backup && <div className="small">Backup of the old tip: <code>{data.backup}</code></div>}
    {data.old_commits !== undefined && <div className="small">Commits since {data.upstream}: {data.old_commits} now, {data.new_commits} after</div>}
    {data.ci && <CiNote ci={data.ci} />}
    <pre className="push-commits">{checks.map((c) => `${c.ok ? "✓" : "✗"} ${c.name}: ${c.detail}`).join("\n")}</pre>
    {result?.ok && <div className="small">{result.reason}</div>}
    {block.started && !result && <div className={busy ? "small" : "push-error"}>{busy ? "Replacing…" : "Started; result unknown. Check the branch before making a new proposal."}</div>}
    {(data.error || result?.reason && !result.ok || error) && <pre className="push-error">{data.error || result?.reason || error}</pre>}
    {!result && !block.started && !data.error && <div className="permission-actions"><button className="button primary small" disabled={busy} onClick={replace}>Replace</button></div>}
  </div>;
}
