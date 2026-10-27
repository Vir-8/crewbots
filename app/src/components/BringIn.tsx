import { useEffect, useState } from "react";
import { api } from "../api";
import type { BroughtIn, FoundFolderOut, FoundMemoryOut, FoundOut } from "../api-types";
import { ago } from "./bits";
import { Icon } from "./Icon";
import "../styles/dialogs.css";

/** Bringing in what Claude Code and Codex already know: the folders you've worked in lately, as projects, and their
 * memories, for the chief to learn from. Nothing comes in that you don't tick; only folders' paths are read from
 * the apps' sessions, never what was said in them. What's in already says so, and isn't ticked unless it's changed
 * since: tick it to bring it in again. */
export function BringIn(props: { chief: string; onDone: (brought: BroughtIn) => void; onClose: () => void }) {
  const [found, setFound] = useState<FoundOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [folders, setFolders] = useState<Set<string>>(new Set()); // ticked to add as projects
  const [memories, setMemories] = useState<Set<string>>(new Set()); // ticked to bring in, by folder (or "" for Codex's own)
  const [bringing, setBringing] = useState(false);

  useEffect(() => {
    api.foundProjects().then(
      (f) => {
        setFound(f);
        setFolders(new Set(f.folders.filter((d) => !d.project).map((d) => d.folder)));
        setMemories(new Set([...f.folders.filter((d) => d.memories.some(isNew)).map((d) => d.folder), ...(f.memories.some(isNew) ? [""] : [])]));
      },
      (e) => setError(String(e).replace(/^Error: /, "")),
    );
    const key = (e: KeyboardEvent) => e.key === "Escape" && props.onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  const toggle = (set: Set<string>, value: string, update: (next: Set<string>) => void) => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    update(next);
  };
  const picked = (found?.folders ?? []).filter((d) => folders.has(d.folder) && !d.project);
  const memoryPaths = [
    ...(found?.folders ?? []).filter((d) => memories.has(d.folder)).flatMap((d) => taken(d.memories)),
    ...(memories.has("") ? taken(found?.memories ?? []) : []),
  ];
  const nothingNew = Boolean(found) && !found!.folders.some((d) => !d.project || d.memories.some(isNew)) && !found!.memories.some(isNew);
  const bring = async () => {
    setBringing(true);
    try {
      props.onDone(await api.bringIn(picked.map((d) => ({ name: d.name, folder: d.folder, about: d.about })), memoryPaths));
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
      setBringing(false);
    }
  };

  return (
    <div className="dialog-backdrop" onClick={props.onClose}>
      <div className="dialog bring-in" role="dialog" aria-label="Bring in context" onClick={(e) => e.stopPropagation()}>
        <h2>Bring in what Claude Code and Codex know</h2>
        <p className="faint">
          The folders you've worked in lately become projects the crew can work in, and their memories go to {props.chief}, who keeps
          what's worth keeping. Only the folders are read from your sessions, never what was said in them.
        </p>
        {error && <p className="bad small">{error}</p>}
        {!found && !error && (
          <div className="updates-working">
            <span className="working-dot" />
            <span>Looking…</span>
          </div>
        )}
        {found && found.folders.length === 0 && found.memories.length === 0 && (
          <p className="faint">Nothing found: neither app has been used in a folder lately.</p>
        )}
        {nothingNew && (found!.folders.length > 0 || found!.memories.length > 0) && (
          <p className="faint">Everything here is in already. Tick a folder's memories to bring them in again.</p>
        )}
        {found && found.folders.length > 0 && (
          <div className="found-list">
            {found.folders.map((d) => (
              <Found
                key={d.folder}
                folder={d}
                adding={folders.has(d.folder)}
                remembering={memories.has(d.folder)}
                onAdd={() => toggle(folders, d.folder, setFolders)}
                onRemember={() => toggle(memories, d.folder, setMemories)}
              />
            ))}
          </div>
        )}
        {found && found.memories.length > 0 && (
          <label className="found-row">
            <input type="checkbox" checked={memories.has("")} onChange={() => toggle(memories, "", setMemories)} />
            <span className="found-main">
              <span className="found-name">Codex's memories</span>
              <span className="faint small">{found.memories.map((m) => m.title).join(" · ")}</span>
              {found.memories.some((m) => m.brought_at) && <span className="faint small">{standing(found.memories)}</span>}
            </span>
          </label>
        )}
        <div className="dialog-actions">
          <button className="button" onClick={props.onClose}>
            Cancel
          </button>
          <button className="button primary" onClick={bring} disabled={bringing || !found || (picked.length === 0 && memoryPaths.length === 0)}>
            {bringing ? "Bringing in…" : !found ? "Bring in" : picked.length && memoryPaths.length ? `Add ${picked.length} and bring ${memoryPaths.length} ${plural(memoryPaths.length)}` : picked.length ? `Add ${picked.length} ${picked.length === 1 ? "project" : "projects"}` : memoryPaths.length ? `Bring ${memoryPaths.length} ${plural(memoryPaths.length)}` : "Nothing to bring in"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Found(props: { folder: FoundFolderOut; adding: boolean; remembering: boolean; onAdd: () => void; onRemember: () => void }) {
  const d = props.folder;
  const from = d.sources.map((s) => (s === "claude" ? "Claude Code" : "Codex")).join(" and ");
  return (
    <div className="found-row">
      <input type="checkbox" checked={Boolean(d.project) || props.adding} disabled={Boolean(d.project)} onChange={props.onAdd} title={d.project ? "Already a project" : "Add it as a project"} />
      <span className="found-main">
        <span className="found-name">
          {d.name}
          {d.project && <span className="pill">Added</span>}
        </span>
        <span className="found-path faint small">{d.folder.replace(/^\/Users\/[^/]+/, "~")}</span>
        {d.about && <span className="found-about small">{d.about}</span>}
        <span className="faint small">
          {from} · {d.sessions} {d.sessions === 1 ? "session" : "sessions"} · {ago(d.last_used)}
        </span>
      </span>
      {d.memories.length > 0 && (
        <label className="found-memories small" title={d.memories.map((m) => `${m.title}${m.changed ? " (changed since)" : m.brought_at ? " (brought in)" : ""}`).join("\n")}>
          <input type="checkbox" checked={props.remembering} onChange={props.onRemember} />
          <Icon name="file" size={12} />
          {d.memories.length} {plural(d.memories.length)}
          {d.memories.some((m) => m.brought_at) && <span className="faint">· {standing(d.memories)}</span>}
        </label>
      )}
    </div>
  );
}

/** A memory not brought in yet, or changed since it was. */
function isNew(memory: FoundMemoryOut): boolean {
  return !memory.brought_at || Boolean(memory.changed);
}

/** What a ticked set of memories brings in: what's new in it, or all of it again when nothing is. */
function taken(memories: FoundMemoryOut[]): string[] {
  return (memories.some(isNew) ? memories.filter(isNew) : memories).map((m) => m.path);
}

/** Where a set of memories stands, once some were brought in: "brought in", "1 changed", "2 new, 1 changed". */
function standing(memories: FoundMemoryOut[]): string {
  const fresh = memories.filter((m) => !m.brought_at).length;
  const changed = memories.filter((m) => m.brought_at && m.changed).length;
  return [fresh ? `${fresh} new` : "", changed ? `${changed} changed` : ""].filter(Boolean).join(", ") || "brought in";
}

function plural(n: number): string {
  return n === 1 ? "memory" : "memories";
}
