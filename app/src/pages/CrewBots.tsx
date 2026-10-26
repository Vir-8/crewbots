import { useEffect, useState } from "react";
import { api } from "../api";
import { Avatar, COLORS, COLOR_NAMES, SHAPE_NAMES, face, labelOf } from "../components/Avatar";
import { Icon } from "../components/Icon";
import { ModelPicker } from "../components/ModelPicker";
import { tokens } from "../components/StatusPanel";
import { Field, Page, Segmented, Sheet } from "../components/ui";
import type { Crew } from "../state";
import type { Bot, Card, McpServerInfo } from "../types";
import "../styles/crew.css";

// The window a bot's sessions compact within; "auto" is the model's whole window (a million tokens on some).
const WINDOWS: [string, string][] = [["200000", "200K"], ["400000", "400K"], ["1000000", "1M"], ["auto", "The model's"]];
const ACCESS: Record<string, string> = { read_only: "Reads only", workspace_write: "Writes in its task's folder", full: "Full access" };

/** Your bots: who they are, what each may do, and an editor for each. Saving rewrites its role card. */
export function CrewBots({ crew, onEdit, onChat }: { crew: Crew; onEdit: (bot: string) => void; onChat: (bot: string) => void }) {
  return (
    <Page
      title="Crew"
      actions={
        <button className="button small" onClick={() => onEdit("")}>
          <Icon name="plus" size={13} />
          New bot
        </button>
      }
    >
      {crew.routines?.crew_error && (
        <div className="crew-problem">
          <Icon name="alert" size={14} />
          <div>
            <div>A role card couldn't be read, so the crew carries on as it was.</div>
            <div className="crew-problem-why">{crew.routines.crew_error}</div>
          </div>
        </div>
      )}
      <div className="bot-grid">
        {crew.bots.map((b) => (
          <BotTile key={b.name} bot={b} working={crew.running.some((t) => t.bot === b.name)} onEdit={() => onEdit(b.name)} onChat={() => onChat(b.name)} />
        ))}
      </div>
    </Page>
  );
}

function BotTile({ bot, working, onEdit, onChat }: { bot: Bot; working: boolean; onEdit: () => void; onChat: () => void }) {
  return (
    <div className="bot-tile" onClick={onEdit} role="button" tabIndex={0}>
      <Avatar name={bot.name} size={48} working={working} />
      <div className="bot-tile-name">{labelOf(bot.name)}</div>
      <div className="faint bot-tile-title">
        {bot.display_name ? `${bot.title} · ` : ""}@{bot.name}
      </div>
      <p className="bot-tile-desc">{bot.description}</p>
      <div className="bot-tile-facts">
        <span>{bot.backend}{bot.model ? ` · ${bot.model}` : ""}</span>
        <span>{ACCESS[bot.access]}</span>
        {bot.network !== false && <span>Network</span>}
        {bot.mcp.length > 0 && <span>{bot.mcp.length} MCP</span>}
      </div>
      <div className="bot-tile-actions">
        <button
          className="button small"
          onClick={(e) => {
            e.stopPropagation();
            onChat();
          }}
        >
          <Icon name="message" size={13} />
          Message
        </button>
        <button className="button small" onClick={onEdit}>
          <Icon name="compose" size={13} />
          Edit
        </button>
      </div>
    </div>
  );
}

type Draft = Partial<Card> & { name: string; hosts?: string; budgetOn?: boolean };

/** Edit a bot, or make a new one (`name` null). */
export function BotEditor({ crew, name, onClose }: { crew: Crew; name: string | null; onClose: () => void }) {
  const creating = name === null;
  const [draft, setDraft] = useState<Draft | null>(
    creating ? { name: "", title: "", description: "", backend: "claude", model: "sonnet", effort: null, access: "read_only", network: true, instructions: "## Mission\n\n\n## Owns\n\n- \n\n## Does not own\n\n- \n\n## Done means\n\n- " } : null,
  );
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  useEffect(() => {
    api.mcpServers().then(
      (list) => {
        setServers(list);
        // A new bot starts with every server the crew has; untick the ones it shouldn't have.
        if (creating) setDraft((d) => (d && d.mcp === undefined ? { ...d, mcp: list.map((s) => s.name) } : d));
      },
      () => setServers([]),
    );
  }, []);

  useEffect(() => {
    if (creating) return;
    api.card(name!).then(
      (card) => setDraft({ ...card, hosts: Array.isArray(card.network) ? card.network.join(", ") : "", budgetOn: Boolean(card.budget) }),
      (e) => setError(String(e)),
    );
  }, [name]);

  const set = (fields: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...fields } : d));
  if (!draft) return <Sheet title="Edit bot" onClose={onClose} footer={null}>{error ? <p className="error-text">{error}</p> : <p className="faint">Loading…</p>}</Sheet>;

  const networkMode = draft.network === true ? "any" : Array.isArray(draft.network) ? "hosts" : "off";
  const preview = face(draft.name || "new", draft.avatar ?? undefined);

  const save = async () => {
    setSaving(true);
    setError(null);
    const fields: Partial<Card> = {
      display_name: draft.display_name ?? "",
      title: draft.title,
      description: draft.description,
      backend: draft.backend,
      model: draft.model ?? "",
      effort: draft.effort ?? "",
      context_window: draft.context_window === 200000 ? null : draft.context_window ?? null, // the default stays off the card
      access: draft.access,
      network: networkMode === "hosts" ? (draft.hosts ?? "").split(/[\s,]+/).filter(Boolean) : networkMode === "any",
      avatar: draft.avatar ?? null,
      budget: draft.budgetOn && draft.budget?.tokens ? draft.budget : null,
      mcp: draft.mcp ?? [],
      instructions: draft.instructions,
    };
    try {
      if (creating) await api.newBot({ ...fields, name: draft.name });
      else await api.saveCard(name!, fields);
      await crew.refresh();
      onClose();
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet
      title={creating ? "New bot" : `Edit ${labelOf(name!)}`}
      onClose={onClose}
      footer={
        <>
          {error && <span className="error-text sheet-error">{error}</span>}
          <span className="grow" />
          <button className="button" onClick={onClose}>
            Cancel
          </button>
          <button className="button primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : creating ? "Create bot" : "Save"}
          </button>
        </>
      }
    >
      <div className="face-picker">
        <Avatar name={draft.name || "new"} size={64} color={preview.color} shape={preview.shape} />
        <div className="face-options">
          <div className="swatches">
            {COLOR_NAMES.map((c) => (
              <button
                key={c}
                className={`swatch ${preview.color === c ? "on" : ""}`}
                style={{ background: COLORS[c] }}
                title={c}
                onClick={() => set({ avatar: { ...draft.avatar, color: c } })}
              />
            ))}
          </div>
          <div className="shapes">
            {SHAPE_NAMES.map((s) => (
              <button key={s} className={`shape ${preview.shape === s ? "on" : ""}`} title={s} onClick={() => set({ avatar: { ...draft.avatar, shape: s } })}>
                <Avatar name={draft.name || "new"} size={24} color={preview.color} shape={s} />
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="field-row">
        <Field label="Name" hint="What you call it.">
          <input value={draft.display_name ?? ""} onChange={(e) => set({ display_name: e.target.value })} placeholder={draft.title || "Roger"} />
        </Field>
        <Field label="Handle" hint={creating ? "For @mentions. Can't change later." : "For @mentions."}>
          {creating ? (
            <input value={draft.name} onChange={(e) => set({ name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} placeholder="designer" />
          ) : (
            <input value={`@${draft.name}`} disabled />
          )}
        </Field>
      </div>
      <Field label="Title">
        <input value={draft.title ?? ""} onChange={(e) => set({ title: e.target.value })} placeholder="Designer" />
      </Field>
      <Field label="Description" hint="One line. It's how the other bots know what this one does.">
        <textarea rows={2} value={draft.description ?? ""} onChange={(e) => set({ description: e.target.value })} />
      </Field>

      <Field label="Runs on">
        <Segmented
          value={(draft.backend ?? "claude").startsWith("codex") ? "codex" : "claude"}
          options={[["claude", "Claude"], ["codex", "Codex"]]}
          onChange={(backend) => set({ backend, model: null, effort: null })}
        />
      </Field>
      <Field label="Model and reasoning" hint="Its chat and every thread it opens use these. Blank: the CLI's own default.">
        <ModelPicker crew={crew} backend={draft.backend ?? "claude"} model={draft.model} effort={draft.effort} onChange={(model, effort) => set({ model, effort })} />
      </Field>

      <Field label="Context window" hint="How much of a conversation it keeps before compacting. Past 70% it carries on in a fresh session, from a briefing. Smaller is quicker and cheaper.">
        <Segmented
          value={String(draft.context_window ?? 200000)}
          options={[...WINDOWS, ...(typeof draft.context_window === "number" && !WINDOWS.some(([v]) => v === String(draft.context_window)) ? [[String(draft.context_window), tokens(draft.context_window)] as [string, string]] : [])]}
          onChange={(v) => set({ context_window: v === "auto" ? "auto" : Number(v) })}
        />
      </Field>

      <Field label="Access">
        <select value={draft.access ?? "read_only"} onChange={(e) => set({ access: e.target.value as Card["access"] })}>
          {Object.entries(ACCESS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Network" hint="Web search and pages, and a writing bot's commands, such as installing dependencies. Your logins never reach it.">
        <Segmented
          value={networkMode}
          options={(draft.backend ?? "").startsWith("codex") ? [["off", "Off"], ["any", "Any host"]] : [["off", "Off"], ["any", "Any host"], ["hosts", "Only these hosts"]]}
          onChange={(mode) => set({ network: mode === "any" ? true : mode === "hosts" ? (draft.hosts ?? "").split(/[\s,]+/).filter(Boolean) : false })}
        />
        {networkMode === "hosts" && <input value={draft.hosts ?? ""} onChange={(e) => set({ hosts: e.target.value, network: e.target.value.split(/[\s,]+/).filter(Boolean) })} placeholder="pypi.org, files.pythonhosted.org" />}
      </Field>
      <Field label="MCP servers" hint={servers.length ? "Only the ones chosen reach it. Add more on the Tools page." : "The crew has none yet: add them on the Tools page."}>
        <span className="chip-choices">
          {[...new Set([...servers.map((s) => s.name), ...(draft.mcp ?? [])])].map((name) => {
            const on = (draft.mcp ?? []).includes(name);
            return (
              <button key={name} type="button" className={`choice ${on ? "on" : ""}`} onClick={() => set({ mcp: on ? (draft.mcp ?? []).filter((n) => n !== name) : [...(draft.mcp ?? []), name] })}>
                {on && <Icon name="check" size={12} />}
                {name}
              </button>
            );
          })}
        </span>
      </Field>
      <Field label="Budget" hint="Its turns wait once it has used this many fresh tokens.">
        <span className="budget-row">
          <Segmented value={draft.budgetOn ? "on" : "off"} options={[["off", "None"], ["on", "Up to"]]} onChange={(v) => set({ budgetOn: v === "on", budget: draft.budget ?? { tokens: 2_000_000, per: "week" } })} />
          {draft.budgetOn && (
            <>
              <input type="number" min={1000} step={100000} value={draft.budget?.tokens ?? 2_000_000} onChange={(e) => set({ budget: { tokens: Number(e.target.value), per: draft.budget?.per ?? "week" } })} />
              <select value={draft.budget?.per ?? "week"} onChange={(e) => set({ budget: { tokens: draft.budget?.tokens ?? 2_000_000, per: e.target.value as "day" | "week" } })}>
                <option value="day">a day</option>
                <option value="week">a week</option>
              </select>
            </>
          )}
        </span>
      </Field>
      <Field label="Role card" hint="Its mission, what it owns and doesn't, and what done means. Every turn, it reads this.">
        <textarea className="mono-area" rows={14} value={draft.instructions ?? ""} onChange={(e) => set({ instructions: e.target.value })} />
      </Field>
    </Sheet>
  );
}
