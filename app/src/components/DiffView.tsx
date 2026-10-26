import { useEffect, useState } from "react";
import { api } from "../api";
import type { Task } from "../types";

export function DiffView({ task }: { task: Task }) {
  const [diff, setDiff] = useState<string | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.changes(task.id).then((r) => setDiff(r.diff), (e) => setError(String(e)));
  }, [task.id, task.updated_at]);

  if (error) return <p className="error-text pad">{error}</p>;
  if (diff === undefined) return <p className="muted pad">Loading…</p>;
  if (diff === null) return <p className="muted pad">This task has no worktree.</p>;
  if (!diff.trim()) return <p className="muted pad">No changes.</p>;
  return <DiffText diff={diff} />;
}

/** A unified diff, a file at a time. */
export function DiffText({ diff }: { diff: string }) {
  const files = diff.split(/^(?=diff --git )/m).filter(Boolean);
  return (
    <div className="diff">
      {files.map((file, i) => {
        const name = /^diff --git a\/(.+?) b\//.exec(file)?.[1] ?? `file ${i + 1}`;
        const lines = file.split("\n");
        const firstHunk = lines.findIndex((l) => l.startsWith("@@"));
        const added = lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
        const removed = lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length;
        return (
          <div key={name + i} className="diff-file">
            <div className="diff-name">
              <span className="mono">{name}</span>
              <span className="add">+{added}</span>
              <span className="del">−{removed}</span>
            </div>
            <pre>
              {lines.slice(Math.max(firstHunk, 0)).map((line, j) => (
                <div key={j} className={lineClass(line)}>
                  {line || " "}
                </div>
              ))}
            </pre>
          </div>
        );
      })}
    </div>
  );
}

function lineClass(line: string): string {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "";
}
