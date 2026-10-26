import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import type { Crew } from "../state";
import { oneLine } from "../state";
import type { Profile as ProfileData } from "../types";
import { ACTIVE } from "../types";
import { Avatar } from "./Avatar";
import { ago } from "./bits";
import { Icon } from "./Icon";

const ACCESS: Record<string, string> = { read_only: "Reads only", workspace_write: "Writes in its task's folder", full: "Full access" };
const SHOWN = 6;

/** A bot, beside your chat with it: what it's doing, what runs on its own, the work it has done, and how it's set up. */
export function Profile(props: { crew: Crew; bot: string; onOpen: (id: string) => void; onClose: () => void }) {
  const { crew } = props;
  const bot = crew.bots.find((b) => b.name === props.bot);
  const [data, setData] = useState<ProfileData | null>(null);
  const [all, setAll] = useState({ threads: false, outputs: false });
  const changed = [...crew.tasks.values()].filter((t) => t.bot === props.bot).map((t) => t.updated_at).sort().at(-1);
  useEffect(() => {
    api.profile(props.bot).then(setData, () => setData(null));
  }, [props.bot, changed]);
  useEffect(() => setAll({ threads: false, outputs: false }), [props.bot]);
  if (!bot) return null;

  const routines = (crew.routines?.routines ?? []).filter((r) => r.bot === bot.name);
  const threads = data?.threads ?? [];
  const outputs = data?.outputs ?? [];
  const network = bot.access === "read_only" ? null : bot.network === true ? "Any host" : Array.isArray(bot.network) && bot.network.length ? bot.network.join(", ") : "Off";

  return (
    <aside className="panel profile">
      <div className="profile-close">
        <button className="icon-button" onClick={props.onClose} title="Close">
          <Icon name="x" size={15} />
        </button>
      </div>
      <div className="profile-body">
        <div className="profile-head">
          <Avatar name={bot.name} size={64} working={data?.working} />
          <div className="profile-name">{bot.title}</div>
          <div className="faint">
            {data?.working ? "Working now" : data?.last_active ? `Active ${ago(data.last_active)}` : bot.name}
          </div>
          <p className="profile-desc">{bot.description}</p>
          {bot.path && (
            <button className="button small" onClick={() => bridge.openPath(bot.path!)}>
              <Icon name="compose" size={13} />
              Edit role card
            </button>
          )}
        </div>

        {routines.length > 0 && (
          <section>
            <h3>Routines</h3>
            {routines.map((r) => (
              <div key={r.name} className="profile-item">
                <Icon name="repeat" size={14} />
                <span className="profile-item-text">{r.name}</span>
                <span className="faint">{!r.enabled ? "Off" : r.next_run_at ? when(r.next_run_at) : "On GitHub"}</span>
              </div>
            ))}
          </section>
        )}

        <section>
          <h3>Threads</h3>
          {threads.length === 0 && <p className="faint small">Work it takes on opens here as threads.</p>}
          {threads.slice(0, all.threads ? undefined : SHOWN).map((t) => (
            <button key={t.id} className="profile-item link" onClick={() => props.onOpen(t.id)}>
              <Icon name="message" size={14} />
              <span className="profile-item-text">{oneLine(t.title, 60)}</span>
              {ACTIVE.includes(t.status) || t.status === "held" ? <span className="unread-dot working" /> : <span className="faint">{ago(t.updated_at, true)}</span>}
            </button>
          ))}
          {threads.length > SHOWN && !all.threads && (
            <button className="text-button show-more" onClick={() => setAll({ ...all, threads: true })}>
              Show {threads.length - SHOWN} more
            </button>
          )}
        </section>

        {outputs.length > 0 && (
          <section>
            <h3>Outputs</h3>
            {outputs.slice(0, all.outputs ? undefined : SHOWN).map((o) => (
              <button key={`${o.task_id}-${o.path}`} className="profile-item link" onClick={() => props.onOpen(o.task_id)} title={o.path}>
                <Icon name="diff" size={14} />
                <span className="profile-item-text">{o.path.split("/").filter(Boolean).at(-1) || o.path}</span>
              </button>
            ))}
            {outputs.length > SHOWN && !all.outputs && (
              <button className="text-button show-more" onClick={() => setAll({ ...all, outputs: true })}>
                Show {outputs.length - SHOWN} more
              </button>
            )}
          </section>
        )}

        <section>
          <h3>Setup</h3>
          <dl className="bot-facts">
            <dt>Runs on</dt>
            <dd>
              {bot.backend}
              {bot.model ? ` · ${bot.model}` : ""}
            </dd>
            <dt>Access</dt>
            <dd>
              {ACCESS[bot.access] ?? bot.access}
              {bot.can_delegate ? " · delegates" : ""}
            </dd>
            {network && (
              <>
                <dt>Network</dt>
                <dd>{network}</dd>
              </>
            )}
            <dt>MCP</dt>
            <dd>{bot.mcp.length ? bot.mcp.join(", ") : "Only the crew's tools"}</dd>
            {bot.skills.length > 0 && (
              <>
                <dt>Skills</dt>
                <dd>{bot.skills.join(", ")}</dd>
              </>
            )}
          </dl>
        </section>
      </div>
    </aside>
  );
}

function when(iso: string): string {
  const d = new Date(iso);
  const days = Math.round((new Date(d.toDateString()).getTime() - new Date(new Date().toDateString()).getTime()) / 86_400_000);
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return days === 0 ? time : days === 1 ? `Tomorrow ${time}` : `${d.toLocaleDateString(undefined, { weekday: "short" })} ${time}`;
}
