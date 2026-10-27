import { useState } from "react";
import { api } from "../api";
import type { Crew } from "../state";
import { labelOf } from "./Avatar";
import { Icon } from "./Icon";
import type { Block } from "./threadBlocks";

type Asked = Extract<Block, { kind: "permission" }>;

/** What a bot asks you to allow: to write in a folder outside its own, to reach the network, or your go-ahead for
 * something big. Allow or not with a button; what you allow is its for the rest of that thread (or chat). Shown in the
 * thread that asks and in the chat its work came from, with the same answer in both. */
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
        setError(String(e).replace(/^Error: /, ""));
        setAnswering(false);
      },
    );
  };
  const lasts = asker?.kind === "chat" || asker?.kind === "seat" ? "In this chat, from its next turn" : "For the rest of this thread";

  return (
    <div className={`ask permission ${answered ? "answered" : ""}`}>
      <div className="ask-head">
        <Icon name={answered ? (block.allowed ? "check" : "x") : "lock"} size={15} />
        {answered ? (block.allowed ? "Allowed" : "Not allowed") : `${labelOf(block.bot)} asks your permission`}
      </div>
      {!block.here && asker && (
        <button className="text-button permission-where" onClick={() => onOpen(asker.id)}>
          In its thread: {asker.title}
        </button>
      )}
      <div className="permission-what">{block.what}</div>
      {block.folder && <div className="permission-folder"><Icon name="folder" size={13} /> {block.folder.replace(/^\/Users\/[^/]+/, "~")}</div>}
      {block.why && <div className="permission-why">{block.why}</div>}
      {error && <p className="bad small">{error}</p>}
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
