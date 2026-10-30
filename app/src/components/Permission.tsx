import { useState } from "react";
import { api, errorText } from "../api";
import type { Crew } from "../state";
import { labelOf } from "./Avatar";
import { Markdown, tilde } from "./bits";
import { Icon } from "./Icon";
import type { Block } from "./threadBlocks";

type Asked = Extract<Block, { kind: "permission" }>;

/** What the chief asks you to allow, for its own work or a teammate's: to write in a folder outside the task's own, to
 * reach the network, or your go-ahead for something big. Allow or not with a button; what you allow is that task's for
 * the rest of it. Shown in the task's thread and in the chat it was asked from, with the same answer in both. */
export function Permission({ crew, block, onOpen }: { crew: Crew; block: Asked; onOpen: (id: string) => void }) {
  const [answering, setAnswering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const asker = crew.tasks.get(block.taskId);
  const answered = block.allowed !== undefined;
  const answer = (allowed: boolean) => {
    setAnswering(true);
    api.answerPermission(block.permission, allowed).then(
      () => setError(null),
      (e) => {
        setError(errorText(e));
        setAnswering(false);
      },
    );
  };
  const lasts = asker?.kind === "chat" || asker?.kind === "seat" ? "In this chat, from its next turn" : "For the rest of this thread";

  return (
    <div className={`ask permission ${answered ? "answered" : ""}`}>
      <div className="ask-head">
        <Icon name={answered ? (block.allowed ? "check" : "x") : "lock"} size={15} />
        {answered ? (block.allowed ? "Allowed" : "Not allowed") : `${labelOf(block.bot)} asks your permission${block.for ? ` for ${labelOf(block.for)}` : ""}`}
      </div>
      {!block.here && asker && (
        <button className="text-button permission-where" onClick={() => onOpen(asker.id)}>
          In its thread: {asker.title}
        </button>
      )}
      <div className="permission-what"><Markdown text={block.what} onSelect={onOpen} /></div>
      {block.folder && <div className="permission-folder"><Icon name="folder" size={13} /> {tilde(block.folder)}</div>}
      {block.why && <div className="permission-why"><Markdown text={block.why} onSelect={onOpen} /></div>}
      {error && <p className="error-text small">{error}</p>}
      {!answered && (
        <div className="permission-actions">
          <button className="button small primary" disabled={answering} onClick={() => answer(true)}>
            Allow
          </button>
          <button className="button small" disabled={answering} onClick={() => answer(false)}>
            Don't allow
          </button>
          {block.ask !== "action" && <span className="faint small">{lasts}</span>}
        </div>
      )}
    </div>
  );
}
