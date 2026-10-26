import { useMemo } from "react";
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
              <ChainCard key={chain.steps[0].task.id} chain={chain} where={where(chain.from)} onOpen={onOpen} />
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
                <ChainCard key={chain.steps[0].task.id} chain={chain} where={where(chain.from)} onOpen={onOpen} />
              ))}
            </section>
          )}
        </>
      )}
    </Page>
  );
}

function ChainCard({ chain, where, onOpen }: { chain: Chain; where: string; onOpen: (id: string) => void }) {
  return (
    <div className="list activity-chain">
      {where && (
        <div className="activity-from faint">
          {where} · {ago(chain.steps[0].task.created_at)}
        </div>
      )}
      {chain.steps.map((step) => (
        <StepRow key={step.task.id} step={step} onOpen={onOpen} />
      ))}
    </div>
  );
}

function StepRow({ step, onOpen }: { step: Step; onOpen: (id: string) => void }) {
  const { task, from, verb } = step;
  const said = verb === "asked" ? "asked" : verb === "review" ? "asked for a review from" : "handed work to";
  return (
    <button className="list-row activity-step" style={{ paddingLeft: 16 + step.depth * 24 }} onClick={() => onOpen(task.id)}>
      <span className="activity-faces">
        <Avatar name={from} size={22} />
        <Avatar name={task.bot} size={22} />
      </span>
      <span className="list-text">
        <span className="faint small">
          {labelOf(from)} {said} {labelOf(task.bot)}
        </span>
        <span className="list-title">{oneLine(task.title, 110)}</span>
      </span>
      <StatusPill status={task.status} />
      <span className="faint small activity-age">{ago(task.updated_at, true)}</span>
    </button>
  );
}
