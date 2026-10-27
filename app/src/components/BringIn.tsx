import { useEffect, useState } from "react";
import { api } from "../api";
import type { BroughtIn, FoundFolderOut, FoundOut } from "../api-types";
import { ago } from "./bits";
import { Icon } from "./Icon";
import "../styles/dialogs.css";

/** Bringing in what Claude Code and Codex already know: the folders you've worked in lately, as projects, and their
 * memories, for the chief to learn from. Nothing comes in that you don't tick; only folders' paths are read from
 * the apps' sessions, never what was said in them. */
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
        const fresh = f.folders.filter((d) => !d.project);
        setFolders(new Set(fresh.map((d) => d.folder)));
        setMemories(new Set([...f.folders.filter((d) => d.memories.length).map((d) => d.folder), ...(f.memories.length ? [""] : [])]));
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
    ...(found?.folders ?? []).filter((d) => memories.has(d.folder)).flatMap((d) => d.memories.map((m) => m.path)),
    ...(memories.has("") ? (found?.memories ?? []).map((m) => m.path) : []),
  ];
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
            </span>
          </label>
        )}
        <div className="dialog-actions">
          <button className="button" onClick={props.onClose}>
            Cancel
          </button>
          <button className="button primary" onClick={bring} disabled={bringing || !found || (picked.length === 0 && memoryPaths.length === 0)}>
            {bringing ? "Bringing in…" : picked.length && memoryPaths.length ? `Add ${picked.length} and bring ${memoryPaths.length} memories` : picked.length ? `Add ${picked.length} ${picked.length === 1 ? "project" : "projects"}` : `Bring ${memoryPaths.length} memories`}
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
        <label className="found-memories small" title={d.memories.map((m) => m.title).join("\n")}>
          <input type="checkbox" checked={props.remembering} onChange={props.onRemember} />
          <Icon name="file" size={12} />
          {d.memories.length} {d.memories.length === 1 ? "memory" : "memories"}
        </label>
      )}
    </div>
  );
}
