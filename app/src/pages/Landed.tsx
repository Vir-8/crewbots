import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { LandedItem, LandedOut } from "../api-types";
import { Avatar, labelOf } from "../components/Avatar";
import { ago, Markdown } from "../components/bits";
import { Icon } from "../components/Icon";
import { Page } from "../components/ui";
import type { Crew } from "../state";
import { oneLine } from "../state";
import "../styles/landed.css";

/** What merged, the latest first, mostly by the crew on its own once reviews passed: to look over after it landed,
 * rather than before, with the evidence it merged on, and a revert for what shouldn't stay. Opening it counts as
 * having looked. */
export function Landed({ crew, onOpen }: { crew: Crew; onOpen: (id: string) => void }) {
  const [landed, setLanded] = useState<LandedOut | null>(null);
  const [error, setError] = useState("");
  // When the user last looked, from the first load, so what was new when the page opened stays marked
  const [since, setSince] = useState<string | null>();
  const load = useCallback(() => api.landed().then((out) => {
    setLanded(out);
    setSince((s) => (s === undefined ? out.seen_at : s));
  }, (e) => setError(String(e).replace(/^Error: /, ""))), []);
  const merges = [...crew.tasks.values()].filter((t) => t.merge_state === "merged").length;
  useEffect(() => { load(); }, [load, merges]);
  const opened = since !== undefined;
  useEffect(() => { if (opened) api.landedSeen().catch(() => undefined); }, [opened]);

  return (
    <Page title="Landed">
      <p className="page-lede">Work that merged, the latest first, with the evidence it merged on. Look it over, and revert what shouldn't stay: a revert is a commit of its own.</p>
      {error && <p className="push-error">{error}</p>}
      <div className="list">
        {landed?.items.length === 0 && <p className="list-empty faint">Nothing has merged yet.</p>}
        {landed?.items.map((item) => <LandedRow key={item.task.id} item={item} isNew={!since || item.merged_at > since} onOpen={onOpen} onChange={load} />)}
      </div>
    </Page>
  );
}

function LandedRow({ item, isNew, onOpen, onChange }: { item: LandedItem; isNew: boolean; onOpen: (id: string) => void; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const { task } = item;
  const revert = async () => {
    if (!window.confirm(`Revert ${oneLine(task.title, 80)}? It adds a commit that undoes ${item.commit?.slice(0, 7)} in ${item.into}.`)) return;
    setBusy(true);
    setFailure("");
    try {
      await api.revert(task.id);
      onChange();
    } catch (e) {
      setFailure(String(e).replace(/^Error: /, ""));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className={`landed ${isNew ? "new" : ""} ${item.reverted ? "reverted" : ""}`}>
      <div className="landed-head">
        <Avatar name={task.bot} size={22} />
        <button className="landed-title" onClick={() => onOpen(task.id)} title="Open its thread">{oneLine(task.title, 140)}</button>
      </div>
      <div className="landed-meta">
        <code>{item.commit?.slice(0, 7)}</code>
        <span className="faint">{labelOf(task.bot)} · {item.automatic ? "merged on its own" : "merged by you"} · {ago(item.merged_at)}</span>
        {item.verdicts.map((v, i) => (
          <span key={i} className={`review-chip ${v.verdict}`} title={v.summary}>
            <Icon name={v.verdict === "pass" ? "check" : "x"} size={12} />
            {labelOf(v.bot)}
          </span>
        ))}
        {item.verdicts.length === 0 && <span className="faint">no review</span>}
      </div>
      {item.evidence.length > 0 ? (
        <ul className="landed-evidence">{item.evidence.map((e, i) => <li key={i}><Markdown text={e} onSelect={onOpen} /></li>)}</ul>
      ) : (
        <div className="faint landed-evidence">No evidence was handed back with it.</div>
      )}
      <div className="landed-foot">
        {item.reverted ? (
          <span className="faint"><Icon name="repeat" size={12} /> Reverted as <code>{String(item.reverted.revert_commit).slice(0, 7)}</code></span>
        ) : (
          <button className="button small" disabled={busy} onClick={revert}>{busy ? "Reverting…" : "Revert"}</button>
        )}
        {failure && <span className="push-error">{failure}</span>}
      </div>
    </div>
  );
}
