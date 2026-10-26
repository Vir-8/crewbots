import { useEffect, useState } from "react";
import { api, bridge } from "../api";
import type { Attachment } from "../types";
import { Icon } from "./Icon";
import "../styles/attachments.css";

/** A file waiting in the message box: uploading, ready (with its id), or refused. */
export type Pending = { key: string; name: string; kind: "image" | "file"; size: number; preview?: string; id?: string; error?: string };

const shown = new Map<string, string>(); // attachment id → data URL, kept so a chat doesn't fetch an image twice

/** Reads files the user picked, pasted or dropped, and keeps each in the crew's folder, reporting as each lands. */
export function upload(files: File[], update: (key: string, fields: Partial<Pending>) => void, add: (p: Pending) => void) {
  for (const file of files) {
    const key = `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 8)}`;
    const kind = file.type.startsWith("image/") ? "image" : "file";
    add({ key, name: file.name || "pasted image", kind, size: file.size });
    const reader = new FileReader();
    reader.onload = async () => {
      const url = String(reader.result);
      if (kind === "image") update(key, { preview: url });
      try {
        const saved = await api.uploadAttachment(file.name || "pasted-image.png", url.slice(url.indexOf(",") + 1));
        if (kind === "image") shown.set(saved.id, url);
        update(key, { id: saved.id });
      } catch (e) {
        update(key, { error: String(e).replace(/^Error: /, "") });
      }
    };
    reader.onerror = () => update(key, { error: "couldn't read it" });
    reader.readAsDataURL(file);
  }
}

/** The files in the message box, above what you're typing. */
export function PendingFiles({ items, onRemove }: { items: Pending[]; onRemove: (key: string) => void }) {
  return (
    <div className="pending-files" onClick={(e) => e.stopPropagation()}>
      {items.map((p) => (
        <span key={p.key} className={`file-chip ${p.error ? "error" : ""} ${p.id ? "" : "uploading"}`} title={p.error ?? p.name}>
          {p.kind === "image" && (p.preview || p.id) ? <Thumb pending={p} /> : <Icon name="file" size={14} />}
          <span className="file-chip-name">{p.error ? `${p.name}: ${p.error}` : p.name}</span>
          <button className="file-chip-x" onClick={() => onRemove(p.key)} title="Remove">
            <Icon name="x" size={11} />
          </button>
        </span>
      ))}
    </div>
  );
}

/** A sent message's attachments: images as pictures, other files as chips that open the file. */
export function MessageFiles({ items }: { items: Attachment[] }) {
  if (!items.length) return null;
  return (
    <div className="message-files">
      {items.map((a) =>
        a.kind === "image" ? (
          <Picture key={a.id} item={a} />
        ) : (
          <button key={a.id} className="file-chip" onClick={() => bridge.openPath(a.path)} title={a.path}>
            <Icon name="file" size={14} />
            <span className="file-chip-name">{a.name}</span>
            <span className="faint small">{size(a.size)}</span>
          </button>
        ),
      )}
    </div>
  );
}

/** An image attachment's picture, asked of the daemon once and kept. */
function useSrc(id: string | undefined, known?: string): string | null {
  const [src, setSrc] = useState(known ?? (id ? shown.get(id) : undefined) ?? null);
  useEffect(() => {
    if (src || !id) return;
    api.attachment(id).then((d) => {
      if (!d?.mime) return; // gone from the crew's folder
      const url = `data:${d.mime};base64,${d.data}`;
      shown.set(id, url);
      setSrc(url);
    }, () => undefined);
  }, [id]);
  return src;
}

/** A waiting image: its preview while it uploads, or, kept from before a reload, asked for by its id. */
function Thumb({ pending }: { pending: Pending }) {
  const src = useSrc(pending.id, pending.preview);
  return src ? <img src={src} alt="" /> : <Icon name="file" size={14} />;
}

function Picture({ item }: { item: Attachment }) {
  const src = useSrc(item.id);
  return (
    <button className="message-picture" onClick={() => bridge.openPath(item.path)} title={item.name}>
      {src ? <img src={src} alt={item.name} /> : <span className="faint small">{item.name}</span>}
    </button>
  );
}

function size(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1000))} KB`;
}
