import { useEffect, useMemo, useState } from "react";
import type { Draft } from "../types";
import { Markdown } from "./bits";
import { Icon } from "./Icon";

type Side = "RIGHT" | "LEFT";
type Row = { kind: "hunk" | "add" | "del" | "ctx"; text: string; old?: number; new?: number };
type File = { path: string; status: "added" | "deleted" | "changed"; binary: boolean; rows: Row[]; added: number; removed: number };
type Spot = { path: string; side: Side; line: number };

const IMAGE = /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i;

/** A pull request's diff with line numbers, where you and the reviewers leave line comments for a review: a draft
 * shows under its line, and a + beside a line starts one. Images it changes show before and after. */
export function PullDiff(props: {
  diff: string;
  drafts: Draft[];
  author: (who: string) => string;
  onAdd: (spot: Spot, body: string) => Promise<unknown>;
  onEdit: (id: number, body: string) => Promise<unknown>;
  onDelete: (id: number) => Promise<unknown>;
  image: (path: string, side: "head" | "base") => Promise<string>;
}) {
  const files = useMemo(() => parse(props.diff), [props.diff]);
  const [open, setOpen] = useState<Spot | null>(null);
  const at = (path: string, side: Side, line: number) => props.drafts.filter((d) => d.path === path && d.side === side && d.line === line);

  return (
    <div className="diff pull-diff">
      {files.map((file) => (
        <div key={file.path} className="diff-file">
          <div className="diff-name">
            <span className="mono">{file.path}</span>
            {file.status !== "changed" && <span className="tag">{file.status === "added" ? "New" : "Deleted"}</span>}
            <span className="add">+{file.added}</span>
            <span className="del">−{file.removed}</span>
          </div>
          {file.binary ? (
            IMAGE.test(file.path) ? <ImageChange file={file} image={props.image} /> : <p className="faint small pad-x pd-binary">A binary file: nothing to show line by line.</p>
          ) : (
            <div className="pd-rows">
              {file.rows.map((row, i) => {
                if (row.kind === "hunk") return <div key={i} className="pd-row hunk"><span className="pd-hunk">{row.text}</span></div>;
                const side: Side = row.kind === "del" ? "LEFT" : "RIGHT";
                const line = side === "LEFT" ? row.old! : row.new!;
                const here = [...at(file.path, side, line), ...(row.kind === "ctx" ? at(file.path, "LEFT", row.old!) : [])];
                const editing = open?.path === file.path && open.side === side && open.line === line;
                return (
                  <div key={i}>
                    <div className={`pd-row ${row.kind}`}>
                      <span className="pd-num">{row.old ?? ""}</span>
                      <span className="pd-num">{row.new ?? ""}</span>
                      <button className="pd-add" title="Comment on this line" onClick={() => setOpen(editing ? null : { path: file.path, side, line })}>
                        <Icon name="plus" size={11} />
                      </button>
                      <span className="pd-code">
                        <span className="pd-mark">{row.kind === "add" ? "+" : row.kind === "del" ? "-" : " "}</span>
                        {row.text || " "}
                      </span>
                    </div>
                    {here.map((d) => (
                      <DraftCard key={d.id} draft={d} author={props.author(d.author)} onEdit={props.onEdit} onDelete={props.onDelete} />
                    ))}
                    {editing && (
                      <DraftEditor
                        onCancel={() => setOpen(null)}
                        onSave={async (body) => {
                          await props.onAdd(open!, body);
                          setOpen(null);
                        }}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function DraftCard({ draft, author, onEdit, onDelete }: { draft: Draft; author: string; onEdit: (id: number, body: string) => Promise<unknown>; onDelete: (id: number) => Promise<unknown> }) {
  const [editing, setEditing] = useState(false);
  return (
    <div className="pd-draft" id={`draft-${draft.id}`}>
      <div className="pd-draft-head">
        <Icon name={draft.author === "user" ? "profile" : "branch"} size={12} />
        <span className="pd-draft-author">{author}</span>
        <span className="tag">Draft</span>
        <span className="grow" />
        {!editing && (
          <>
            <button className="text-button" onClick={() => setEditing(true)}>
              Edit
            </button>
            <button className="text-button" onClick={() => onDelete(draft.id)}>
              Delete
            </button>
          </>
        )}
      </div>
      {editing ? (
        <DraftEditor
          initial={draft.body}
          saveLabel="Save"
          onCancel={() => setEditing(false)}
          onSave={async (body) => {
            await onEdit(draft.id, body);
            setEditing(false);
          }}
        />
      ) : (
        <div className="pd-draft-body">
          <Markdown text={draft.body} onSelect={() => undefined} />
        </div>
      )}
    </div>
  );
}

function DraftEditor({ initial = "", saveLabel = "Add to review", onSave, onCancel }: { initial?: string; saveLabel?: string; onSave: (body: string) => Promise<unknown>; onCancel: () => void }) {
  const [body, setBody] = useState(initial);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!body.trim() || saving) return;
    setSaving(true);
    setError(null);
    try {
      await onSave(body.trim());
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
      setSaving(false);
    }
  };
  return (
    <div className="pd-editor">
      <textarea
        autoFocus
        rows={3}
        value={body}
        placeholder="Leave a comment on this line"
        onChange={(e) => setBody(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
          if (e.key === "Escape") onCancel();
        }}
      />
      {error && <p className="error-text small">{error}</p>}
      <div className="form-actions">
        <span className="faint small">Private until you submit your review. ⌘↩ saves.</span>
        <button className="button small" onClick={onCancel}>
          Cancel
        </button>
        <button className="button small primary" disabled={!body.trim() || saving} onClick={save}>
          {saveLabel}
        </button>
      </div>
    </div>
  );
}

function ImageChange({ file, image }: { file: File; image: (path: string, side: "head" | "base") => Promise<string> }) {
  const [before, setBefore] = useState<string | null>(null);
  const [after, setAfter] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const fail = (e: unknown) => setError(String(e).replace(/^Error: /, ""));
    if (file.status !== "added") image(file.path, "base").then(setBefore, fail);
    if (file.status !== "deleted") image(file.path, "head").then(setAfter, fail);
  }, [file.path]);
  return (
    <div className="pd-images">
      {error && <p className="error-text small">{error}</p>}
      {file.status !== "added" && (
        <figure>
          <figcaption className="faint small">Before</figcaption>
          {before ? <img src={before} alt={`${file.path}, before`} /> : <span className="faint small">Loading…</span>}
        </figure>
      )}
      {file.status !== "deleted" && (
        <figure>
          <figcaption className="faint small">{file.status === "added" ? "Added" : "After"}</figcaption>
          {after ? <img src={after} alt={`${file.path}, after`} /> : <span className="faint small">Loading…</span>}
        </figure>
      )}
    </div>
  );
}

/** A unified diff into files and numbered rows. */
function parse(diff: string): File[] {
  const files: File[] = [];
  for (const chunk of diff.split(/^(?=diff --git )/m).filter(Boolean)) {
    const lines = chunk.split("\n");
    const name = /^diff --git a\/(.+?) b\/(.+)$/.exec(lines[0]);
    const file: File = {
      path: name?.[2] ?? name?.[1] ?? "file",
      status: lines.some((l) => l.startsWith("new file mode")) ? "added" : lines.some((l) => l.startsWith("deleted file mode")) ? "deleted" : "changed",
      binary: lines.some((l) => l.startsWith("Binary files ") || l === "GIT binary patch"),
      rows: [],
      added: 0,
      removed: 0,
    };
    let old = 0;
    let neu = 0;
    let inHunk = false;
    for (const line of lines.slice(1)) {
      const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/.exec(line);
      if (hunk) {
        old = Number(hunk[1]);
        neu = Number(hunk[2]);
        inHunk = true;
        file.rows.push({ kind: "hunk", text: line });
      } else if (!inHunk || line.startsWith("\\")) {
        continue; // the file's header lines, and "No newline at end of file"
      } else if (line.startsWith("+")) {
        file.rows.push({ kind: "add", text: line.slice(1), new: neu++ });
        file.added++;
      } else if (line.startsWith("-")) {
        file.rows.push({ kind: "del", text: line.slice(1), old: old++ });
        file.removed++;
      } else if (line.startsWith(" ")) {
        file.rows.push({ kind: "ctx", text: line.slice(1), old: old++, new: neu++ }); // git writes a blank one as " "
      }
    }
    files.push(file);
  }
  return files;
}
