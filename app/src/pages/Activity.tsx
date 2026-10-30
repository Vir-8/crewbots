import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { type Chain, chains, pairs, type Step } from "../activity";
import { Avatar, labelOf } from "../components/Avatar";
import { ago, StatusPill } from "../components/bits";
import { Icon } from "../components/Icon";
import { Page } from "../components/ui";
import type { Crew } from "../state";
import { oneLine } from "../state";
import type { Task } from "../types";
import "../styles/activity.css";

const RECENT = 20;
const WEEK = 7 * 86_400_000;

/** What the bots hand each other, as it happens: work they delegate, reviews they ask for and questions, each chain
 * from where it started, so agent-to-agent work is never out of sight. */
export function Activity({ crew, onOpen }: { crew: Crew; onOpen: (id: string) => void }) {
  const all = useMemo(() => chains(crew.tasks), [crew.tasks]);
  const week = useMemo(() => pairs(crew.tasks, new Date(Date.now() - WEEK).toISOString()), [crew.tasks]);
  const busy = all.filter((c) => c.busy);
  const done = all.filter((c) => !c.busy).slice(0, RECENT);
  const mergedIds = useMemo(() => all.flatMap((chain) => chain.steps.map((s) => s.task)).filter((t) => t.merge_state === "merged").map((t) => t.id).sort().join(","), [all]);
  const [automatic, setAutomatic] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!mergedIds) return;
    let current = true;
    api.landed().then((landed) => {
      if (current) setAutomatic(new Set(landed.items.filter((item) => item.automatic).map((item) => item.task.id)));
    }, () => {});
    return () => { current = false; };
  }, [mergedIds]);
  const where = (task: Task | undefined) => {
    if (!task) return "";
    if (task.kind === "chat") return `From ${labelOf(task.bot)}'s chat`;
    if (task.kind === "seat") return `From the group chat ${crew.chats.find((c) => c.room?.id === task.room_id)?.title ?? task.title}`;
    return `From ${oneLine(task.title, 70)}`;
  };

  return (
    <Page title="Activity">
      <p className="page-lede">What your bots hand each other: work they delegate, reviews they ask for, and questions, as it happens.</p>
      {all.length === 0 ? (
        <div className="list">
          <p className="list-empty faint">No bot has handed another anything yet. When one delegates, asks for a review or asks a teammate a question, it shows here.</p>
        </div>
      ) : (
        <>
          <section className="page-section">
            <h2>In flight</h2>
            {busy.length === 0 && (
              <div className="list">
                <p className="list-empty faint">Nothing is passing between bots right now.</p>
              </div>
            )}
            {busy.map((chain) => (
              <ChainCard key={chain.steps[0].task.id} chain={chain} where={where(chain.from)} onOpen={onOpen} automatic={automatic} />
            ))}
          </section>
          {week.length > 0 && (
            <section className="page-section">
              <h2>Who works with whom this week</h2>
              <div className="activity-pairs">
                {week.map((p) => (
                  <span key={`${p.from}>${p.to}`} className="activity-pair">
                    <Avatar name={p.from} size={18} />
                    {labelOf(p.from)}
                    <Icon name="chevronRight" size={12} className="faint" />
                    <Avatar name={p.to} size={18} />
                    {labelOf(p.to)}
                    <span className="faint">{p.count}</span>
                  </span>
                ))}
              </div>
            </section>
          )}
          {done.length > 0 && (
            <section className="page-section">
              <h2>Recently</h2>
              {done.map((chain) => (
                <ChainCard key={chain.steps[0].task.id} chain={chain} where={where(chain.from)} onOpen={onOpen} automatic={automatic} />
              ))}
            </section>
          )}
        </>
      )}
    </Page>
  );
}

function ChainCard({ chain, where, onOpen, automatic }: { chain: Chain; where: string; onOpen: (id: string) => void; automatic: Set<string> }) {
  return (
    <div className="list activity-chain">
      {where && (
        <div className="activity-from faint">
          {where} · {ago(chain.steps[0].task.created_at)}
        </div>
      )}
      {chain.steps.map((step) => (
        <StepRow key={step.task.id} step={step} onOpen={onOpen} automatic={automatic.has(step.task.id)} />
      ))}
    </div>
  );
}

const SAID: Record<Step["verb"], string> = { asked: "asked", review: "asked for a review from", delegated: "handed work to" };

function StepRow({ step, onOpen, automatic }: { step: Step; onOpen: (id: string) => void; automatic: boolean }) {
  const { task, from, verb } = step;
  return (
    <button className="list-row activity-step" style={{ paddingLeft: 16 + step.depth * 24 }} onClick={() => onOpen(task.id)}>
      <span className="activity-faces" aria-hidden>
        <Avatar name={from} size={24} />
        <Icon name="chevronRight" size={12} className="faint" />
        <Avatar name={task.bot} size={24} />
      </span>
      <span className="list-text">
        <span className="list-title">{oneLine(task.title, 110)}</span>
        <span className="faint small">
          {labelOf(from)} {SAID[verb]} {labelOf(task.bot)}
          {automatic && " · Merged automatically"}
        </span>
      </span>
      <span className="activity-state">
        <StatusPill status={task.status} />
        <span className="faint small">{ago(task.updated_at, true)}</span>
      </span>
    </button>
  );
}
