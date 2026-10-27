import { type ReactNode, useEffect, useState } from "react";
import { api, bridge } from "../api";
import { TEXT, highlight } from "../highlight";
import type { Attachment } from "../types";
import { Icon } from "./Icon";
import "../styles/attachments.css";

/** A file waiting in the message box: uploading, ready (with its id), or refused. */
export type Pending = { key: string; name: string; kind: "image" | "file"; size: number; preview?: string; id?: string; error?: string };

const shown = new Map<string, string>(); // attachment id → data URL, for a picture in the message box

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

/** A waiting image: its preview while it uploads, or, kept from before a reload, asked for by its id. */
function Thumb({ pending }: { pending: Pending }) {
  const [src, setSrc] = useState(pending.preview ?? (pending.id ? shown.get(pending.id) : undefined) ?? null);
  useEffect(() => {
    if (src || !pending.id) return;
    const id = pending.id;
    api.attachment(id).then((d) => {
      if (!d?.mime) return; // gone from the crew's folder
      const url = `data:${d.mime};base64,${d.data}`;
      shown.set(id, url);
      setSrc(url);
    }, () => undefined);
  }, [pending.id]);
  return src ? <img src={src} alt="" /> : <Icon name="file" size={14} />;
}

export type Shown = "image" | "video" | "audio" | "pdf" | "html" | "text" | "other";

/** How a file is shown, by its extension first (a .ts file is code, not a video stream) and then its MIME type. */
export function shownAs(file: { name: string; mime: string }): Shown {
  const ext = extension(file.name);
  if (TEXT.has(ext) && !["html", "htm", "svg"].includes(ext)) return "text";
  const mime = file.mime;
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "audio";
  if (mime === "application/pdf") return "pdf";
  if (mime === "text/html") return "html";
  if (mime.startsWith("text/") || /json|xml|yaml|toml|javascript/.test(mime)) return "text";
  return "other";
}

/** Files under a message: what you attached, or what a bot shared. Pictures, players, a PDF's first page, a page,
 * the start of a text file; anything else as a chip that opens it. */
export function Files({ items, mine, compact }: { items: Attachment[]; mine?: boolean; compact?: boolean }) {
  const [enlarged, setEnlarged] = useState<Attachment | null>(null);
  if (!items.length) return null;
  return (
    <div className={`message-files ${mine ? "mine" : ""} ${compact ? "compact" : ""}`}>
      {items.map((file) => (
        <FileView key={file.id || file.path} file={file} onEnlarge={() => setEnlarged(file)} />
      ))}
      {enlarged && <Lightbox file={enlarged} onClose={() => setEnlarged(null)} />}
    </div>
  );
}

function FileView({ file, onEnlarge }: { file: Attachment; onEnlarge: () => void }) {
  const [broken, setBroken] = useState(false);
  const src = bridge.fileSrc(file.path);
  const as = broken ? "other" : shownAs(file);
  switch (as) {
    case "image":
      return (
        <button className="message-picture" onClick={onEnlarge} title={file.name}>
          <img src={src} alt={file.name} onError={() => setBroken(true)} />
        </button>
      );
    case "video":
      return (
        <Card file={file}>
          <video className="file-video" src={src} controls preload="metadata" onError={() => setBroken(true)} />
        </Card>
      );
    case "audio":
      return (
        <Card file={file}>
          <audio className="file-audio" src={src} controls preload="metadata" onError={() => setBroken(true)} />
        </Card>
      );
    case "pdf":
      return <PdfCard file={file} />;
    case "html":
      return <PageCard file={file} src={src} />;
    case "text":
      return file.size <= TEXT_BYTES ? <TextCard file={file} src={src} /> : <Chip file={file} />;
    default:
      return <Chip file={file} />;
  }
}

/** A file's name and size, with Open (in its own app) and Show in Finder. */
function Card({ file, children, extra, className = "" }: { file: Attachment; children: ReactNode; extra?: ReactNode; className?: string }) {
  return (
    <div className={`file-card ${className}`}>
      <div className="file-card-head">
        <Icon name="file" size={13} />
        <span className="file-card-name" title={file.path}>
          {file.name}
        </span>
        <span className="faint small">{size(file.size)}</span>
        <span className="grow" />
        {extra}
        <button className="text-button small" onClick={() => bridge.openFile(file.path)} title="Open it in its own app (a file that could run is shown in Finder instead)">
          Open
        </button>
        <button className="icon-button small" onClick={() => bridge.reveal(file.path)} title="Show in Finder">
          <Icon name="folder" size={13} />
        </button>
      </div>
      {children}
    </div>
  );
}

function Chip({ file }: { file: Attachment }) {
  return (
    <button className="file-chip" onClick={() => bridge.openFile(file.path)} title={file.path}>
      <Icon name="file" size={14} />
      <span className="file-chip-name">{file.name}</span>
      <span className="faint small">{size(file.size)}</span>
    </button>
  );
}

/** A PDF: its first page, from Quick Look, and a click opens it. */
function PdfCard({ file }: { file: Attachment }) {
  const [thumb, setThumb] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let live = true;
    bridge.thumbnail(file.path, 480).then((path) => live && setThumb(path ? bridge.fileSrc(path) : null), () => live && setThumb(null));
    return () => {
      live = false;
    };
  }, [file.path]);
  return (
    <Card file={file} className="pdf">
      <button className="file-pdf-page" onClick={() => bridge.openFile(file.path)} title="Open">
        {thumb ? <img src={thumb} alt={`The first page of ${file.name}`} /> : <span className="faint small">{thumb === null ? "PDF" : ""}</span>}
      </button>
    </Card>
  );
}

/** An HTML page, drawn in a sandbox with no scripts unless you let them run; it can't reach the app either way. */
function PageCard({ file, src }: { file: Attachment; src: string }) {
  const [scripts, setScripts] = useState(false);
  const toggle = (
    <button className={`text-button small ${scripts ? "on" : ""}`} onClick={() => setScripts(!scripts)} title={scripts ? "Draw it again without its scripts" : "Let its scripts run, still in a sandbox"}>
      {scripts ? "Scripts on" : "Run scripts"}
    </button>
  );
  return (
    <Card file={file} extra={toggle} className="page">
      <iframe key={String(scripts)} className="file-page" src={src} sandbox={scripts ? "allow-scripts" : ""} title={file.name} referrerPolicy="no-referrer" />
    </Card>
  );
}

const TEXT_BYTES = 1_000_000; // bigger text files are a chip that opens them
const FIRST_LINES = 14;
const MOST_LINES = 3000;

/** A text or code file: its first lines, lightly coloured, and the rest on request. */
function TextCard({ file, src }: { file: Attachment; src: string }) {
  const [text, setText] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  useEffect(() => {
    let live = true;
    fetch(src)
      .then((r) => (r.ok ? r.text() : Promise.reject(r.status)))
      .then((t) => live && setText(t), () => live && setText(""));
    return () => {
      live = false;
    };
  }, [src]);
  const lines = text?.split("\n") ?? [];
  const showing = lines.slice(0, all ? MOST_LINES : FIRST_LINES).join("\n");
  const more = lines.length > FIRST_LINES;
  return (
    <Card file={file} className="text">
      <pre className={`file-code ${all ? "all" : ""}`}>
        {text === null ? " " : highlight(showing, extension(file.name)).map((t, i) => (t.kind ? <span key={i} className={`tok-${t.kind}`}>{t.text}</span> : t.text))}
      </pre>
      {more && (
        <button className="text-button small file-more" onClick={() => setAll(!all)}>
          {all ? "Less" : `All ${lines.length.toLocaleString()} lines`}
        </button>
      )}
    </Card>
  );
}

/** A picture, big, over the window: Escape or a click outside closes it. */
function Lightbox({ file, onClose }: { file: Attachment; onClose: () => void }) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="lightbox" onClick={onClose} role="dialog" aria-label={file.name}>
      <div className="lightbox-bar" onClick={(e) => e.stopPropagation()}>
        <span className="file-card-name">{file.name}</span>
        <span className="faint small">{size(file.size)}</span>
        <span className="grow" />
        <button className="button small" onClick={() => bridge.openFile(file.path)}>
          Open
        </button>
        <button className="button small" onClick={() => bridge.reveal(file.path)}>
          Show in Finder
        </button>
        <button className="icon-button" onClick={onClose} title="Close (Esc)">
          <Icon name="x" size={14} />
        </button>
      </div>
      <img className="lightbox-image" src={bridge.fileSrc(file.path)} alt={file.name} onClick={(e) => e.stopPropagation()} />
    </div>
  );
}

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : name.toLowerCase();
}

function size(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1000))} KB`;
}
