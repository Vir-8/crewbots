import { type ReactNode, useEffect, useState } from "react";
import { api } from "../api";
import type { Crew } from "../state";
import { oneLine } from "../state";
import type { Profile as ProfileData, Room } from "../types";
import { ACTIVE } from "../types";
import { Avatar, GroupAvatar, labelOf } from "./Avatar";
import { ago } from "./bits";
import { Icon } from "./Icon";

const ACCESS: Record<string, string> = { read_only: "Reads only", workspace_write: "Writes in its task's folder", full: "Full access" };
const SHOWN = 6;

/** A bot, beside your chat with it: what it's doing, what runs on its own, the work it has done, and how it's set up. */
export function Profile(props: { crew: Crew; bot: string; onOpen: (id: string) => void; onClose: () => void; onEdit: () => void; edge?: ReactNode }) {
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
  const network = bot.network === true ? "Any host" : Array.isArray(bot.network) && bot.network.length ? bot.network.join(", ") : "Off";

  return (
    <aside className="panel profile">
      {props.edge}
      <div className="profile-close">
        <button className="icon-button" onClick={props.onClose} title="Close">
          <Icon name="x" size={15} />
        </button>
      </div>
      <div className="profile-body">
        <div className="profile-head">
          <Avatar name={bot.name} size={64} working={data?.working} />
          <div className="profile-name">{labelOf(bot.name)}</div>
          {bot.display_name && <div className="faint">{bot.title}</div>}
          <div className="faint">
            {data?.working ? "Working now" : data?.last_active ? `Active ${ago(data.last_active)}` : bot.name}
          </div>
          <p className="profile-desc">{bot.description}</p>
          <button className="button small" onClick={props.onEdit}>
            <Icon name="compose" size={13} />
            Edit
          </button>
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

/** A group chat, beside it: who's in it and what each is doing, who leads, and the threads opened from it. */
export function RoomProfile(props: { crew: Crew; room: Room; onOpen: (id: string) => void; onChat: (bot: string) => void; onClose: () => void; edge?: ReactNode }) {
  const { crew, room } = props;
  const [all, setAll] = useState(false);
  useEffect(() => setAll(false), [room.id]);
  const seats = [...crew.tasks.values()].filter((t) => t.kind === "seat" && t.room_id === room.id);
  const threads = seats
    .flatMap((seat) => crew.family(seat.id))
    .filter((t) => t.kind === "thread")
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const members = [room.lead, ...room.members.filter((m) => m !== room.lead)];

  return (
    <aside className="panel profile">
      {props.edge}
      <div className="profile-close">
        <button className="icon-button" onClick={props.onClose} title="Close">
          <Icon name="x" size={15} />
        </button>
      </div>
      <div className="profile-body">
        <div className="profile-head">
          <GroupAvatar members={room.members} size={64} />
          <div className="profile-name">{room.title}</div>
          <div className="faint">
            {room.members.length} bots · {labelOf(room.lead)} leads
          </div>
          <p className="profile-desc">
            {labelOf(room.lead)} answers what you say here, and anyone you @mention joins in. Bots bring each other in the same way.
          </p>
        </div>

        <section>
          <h3>Members</h3>
          {members.map((name) => {
            const bot = crew.bots.find((b) => b.name === name);
            const seat = seats.find((s) => s.bot === name);
            const replying = seat && ACTIVE.includes(seat.status);
            return (
              <button key={name} className="profile-item link member" onClick={() => props.onChat(name)} title={`Chat with ${labelOf(name)}`}>
                <Avatar name={name} size={26} working={Boolean(replying)} />
                <span className="profile-item-text">
                  <span className="member-name">{labelOf(name)}</span>
                  {bot?.display_name && <span className="faint small">{bot.title}</span>}
                </span>
                {replying ? <span className="faint small">Replying…</span> : name === room.lead ? <span className="lead-tag">Lead</span> : null}
              </button>
            );
          })}
        </section>

        <section>
          <h3>Threads</h3>
          {threads.length === 0 && <p className="faint small">Work the group hands out opens here as threads.</p>}
          {threads.slice(0, all ? undefined : SHOWN).map((t) => (
            <button key={t.id} className="profile-item link" onClick={() => props.onOpen(t.id)}>
              <Avatar name={t.bot} size={16} />
              <span className="profile-item-text">{oneLine(t.title, 60)}</span>
              {ACTIVE.includes(t.status) || t.status === "held" ? <span className="unread-dot working" /> : <span className="faint">{ago(t.updated_at, true)}</span>}
            </button>
          ))}
          {threads.length > SHOWN && !all && (
            <button className="text-button show-more" onClick={() => setAll(true)}>
              Show {threads.length - SHOWN} more
            </button>
          )}
        </section>
      </div>
    </aside>
  );
}
