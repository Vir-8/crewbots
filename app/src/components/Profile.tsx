import { type ReactNode, useEffect, useState } from "react";
import { api } from "../api";
import type { Crew } from "../state";
import { oneLine } from "../state";
import type { PageOut } from "../api-types";
import type { Profile as ProfileData, Room, RoomChange } from "../types";
import { ACTIVE } from "../types";
import { Avatar, GroupAvatar, labelOf } from "./Avatar";
import { ago } from "./bits";
import { Icon } from "./Icon";
import { Switch } from "./ui";
import "../styles/panels.css";

const ACCESS: Record<string, string> = { read_only: "Reads only", workspace_write: "Writes in its task's folder", full: "Full access" };
const SHOWN = 6;

/** A bot, beside your chat with it: what it's doing, its pages, what runs on its own, the work it has done, and how
 * it's set up. */
export function Profile(props: { crew: Crew; bot: string; onOpen: (id: string) => void; onPage: (page: string) => void; onClose: () => void; onEdit: () => void; edge?: ReactNode }) {
  const { crew } = props;
  const bot = crew.bots.find((b) => b.name === props.bot);
  const [data, setData] = useState<ProfileData | null>(null);
  const [pages, setPages] = useState<PageOut[]>([]);
  const [all, setAll] = useState({ threads: false, outputs: false, pages: false });
  const stamp = crew.pageStamp(props.bot);
  useEffect(() => {
    api.pages(props.bot).then(setPages, () => setPages([]));
  }, [props.bot, stamp]);
  const changed = [...crew.tasks.values()].filter((t) => t.bot === props.bot).map((t) => t.updated_at).sort().at(-1);
  useEffect(() => {
    api.profile(props.bot).then(setData, () => setData(null));
  }, [props.bot, changed]);
  useEffect(() => setAll({ threads: false, outputs: false, pages: false }), [props.bot]);
  if (!bot) return null;

  const routines = (crew.routines?.routines ?? []).filter((r) => r.bot === bot.name);
  const threads = data?.threads ?? [];
  const outputs = data?.outputs ?? [];
  const network = bot.network === true ? "Any host" : Array.isArray(bot.network) && bot.network.length ? bot.network.join(", ") : "Off";

  return (
    <aside className="panel profile">
      {props.edge}
      <div className="panel-drag" data-tauri-drag-region />
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

        <section>
          <h3>Pages</h3>
          {!pages.some((p) => p.name === "Scratchpad") && (
            <button className="profile-item link" onClick={() => props.onPage("Scratchpad")}>
              <Icon name="file" size={14} />
              <span className="profile-item-text">Scratchpad</span>
              <span className="faint">Not started</span>
            </button>
          )}
          {pages.slice(0, all.pages ? undefined : SHOWN).map((p) => (
            <button key={p.name} className="profile-item link" onClick={() => props.onPage(p.name)} title={p.path}>
              <Icon name="file" size={14} />
              <span className="profile-item-text">{p.name}</span>
              <span className="faint">{ago(p.updated, true)}</span>
            </button>
          ))}
          {pages.length > SHOWN && !all.pages && (
            <button className="text-button show-more" onClick={() => setAll({ ...all, pages: true })}>
              Show {pages.length - SHOWN} more
            </button>
          )}
        </section>

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

/** A group chat, beside it: who's in it and what each is doing, who leads, the threads opened from it, and its
 * settings: its name, its bots and lead, how long bots reply to each other, muting, archiving and deleting it. */
export function RoomProfile(props: { crew: Crew; room: Room; onOpen: (id: string) => void; onChat: (bot: string) => void; onClose: () => void; onGone: () => void; edge?: ReactNode }) {
  const { crew, room } = props;
  const [all, setAll] = useState(false);
  const [naming, setNaming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  useEffect(() => {
    setAll(false);
    setNaming(null);
    setDeleting(false);
  }, [room.id]);
  const seats = [...crew.tasks.values()].filter((t) => t.kind === "seat" && t.room_id === room.id);
  const threads = seats
    .flatMap((seat) => crew.family(seat.id))
    .filter((t) => t.kind === "thread")
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const members = [room.lead, ...room.members.filter((m) => m !== room.lead)];
  const others = crew.bots.filter((b) => !room.members.includes(b.name));
  const change = (fields: RoomChange) => crew.changeRoom(room.id, fields).catch(() => undefined);
  const rename = () => {
    if (naming?.trim() && naming.trim() !== room.title) change({ title: naming.trim() });
    setNaming(null);
  };

  return (
    <aside className="panel profile">
      {props.edge}
      <div className="panel-drag" data-tauri-drag-region />
      <div className="profile-close">
        <button className="icon-button" onClick={props.onClose} title="Close">
          <Icon name="x" size={15} />
        </button>
      </div>
      <div className="profile-body">
        <div className="profile-head">
          <GroupAvatar members={room.members} size={64} />
          {naming === null ? (
            <button className="profile-name room-name" onClick={() => setNaming(room.title)} title="Rename the group">
              {room.title}
            </button>
          ) : (
            <input
              className="room-name-input"
              autoFocus
              value={naming}
              onChange={(e) => setNaming(e.target.value)}
              onBlur={rename}
              onKeyDown={(e) => {
                if (e.key === "Enter") rename();
                if (e.key === "Escape") setNaming(null);
              }}
            />
          )}
          <div className="faint">
            {room.members.length} bots · {labelOf(room.lead)} leads{room.muted ? " · Muted" : ""}
            {room.archived_at ? " · Archived" : ""}
          </div>
          <p className="profile-desc">
            {labelOf(room.lead)} answers what you say here or hands it on, and anyone you @mention joins in. Bots bring each other in the same way.
          </p>
        </div>

        <section>
          <h3>Members</h3>
          {members.map((name) => {
            const bot = crew.bots.find((b) => b.name === name);
            const seat = seats.find((s) => s.bot === name);
            const replying = seat?.status === "running" && !seat.attempt;
            const retrying = seat?.attempt;
            return (
              <div key={name} className="member-row">
                <button className="profile-item link member" onClick={() => props.onChat(name)} title={`Chat with ${labelOf(name)}`}>
                  <Avatar name={name} size={26} working={Boolean(replying)} />
                  <span className="profile-item-text">
                    <span className="member-name">{labelOf(name)}</span>
                    {bot?.display_name && <span className="faint small">{bot.title}</span>}
                  </span>
                  {replying ? <span className="faint small">Working…</span> : retrying ? <span className="faint small">Retrying ({retrying.attempt} of {retrying.of})</span> : name === room.lead ? <span className="lead-tag">Lead</span> : null}
                </button>
                {name !== room.lead && !replying && (
                  <span className="member-actions">
                    <button className="text-button" onClick={() => change({ lead: name })}>
                      Make lead
                    </button>
                    <button className="icon-button" onClick={() => change({ members: room.members.filter((m) => m !== name) })} title={`Take ${labelOf(name)} out of the group`}>
                      <Icon name="x" size={13} />
                    </button>
                  </span>
                )}
              </div>
            );
          })}
          {others.length > 0 && (
            <select className="member-add" value="" onChange={(e) => e.target.value && change({ members: [...room.members, e.target.value] })}>
              <option value="">Add a bot…</option>
              {others.map((b) => (
                <option key={b.name} value={b.name}>
                  {labelOf(b.name)}
                </option>
              ))}
            </select>
          )}
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

        <section>
          <h3>Settings</h3>
          <label className="room-setting">
            <span className="room-setting-label">Bots reply to each other</span>
            <select value={room.max_depth ?? ""} onChange={(e) => change({ max_depth: e.target.value ? Number(e.target.value) : null })}>
              <option value="">No limit</option>
              {Array.from(new Set([...Array.from({ length: 20 }, (_, i) => i + 1), 50, 100, ...(room.max_depth ? [room.max_depth] : [])])).sort((a, b) => a - b).map((n) => (
                <option key={n} value={n}>
                  {n === 1 ? "Once" : `${n} times`}
                </option>
              ))}
            </select>
          </label>
          <div className="room-setting">
            <span className="room-setting-label">Mute notifications</span>
            <Switch on={Boolean(room.muted)} onChange={(muted) => change({ muted })} label="Mute notifications" />
          </div>
          {!room.crew && (
            <div className="room-actions">
              <button className="button small" onClick={() => change({ archived: !room.archived_at })}>
                <Icon name="archive" size={14} />
                {room.archived_at ? "Unarchive" : "Archive"}
              </button>
              {deleting ? (
                <span className="room-delete">
                  Delete it and its messages? Work it handed out stays.
                  <button className="button small" onClick={() => setDeleting(false)}>
                    Cancel
                  </button>
                  <button className="button small danger" onClick={() => crew.deleteRoom(room.id).then(props.onGone, () => undefined)}>
                    Delete
                  </button>
                </span>
              ) : (
                <button className="button small danger-text" onClick={() => setDeleting(true)}>
                  Delete…
                </button>
              )}
            </div>
          )}
        </section>
      </div>
    </aside>
  );
}
