import { useState } from "react";
import type { Crew } from "../state";
import { Markdown } from "./bits";
import { Marked } from "./Find";
import { Icon } from "./Icon";
import type { Block } from "./threadBlocks";

type Asked = Extract<Block, { kind: "ask" }>;

/** A bot's question, waiting on your answer. With choices, a button for each (boxes to tick, when several may be
 * picked) and Other, to answer in your own words. Answering sends your message, as if you'd typed it; once answered,
 * what you picked stays marked. */
export function Question({ crew, block, onOpen }: { crew: Crew; block: Asked; onOpen: (id: string) => void }) {
  const choices = block.choices ?? [];
  const [picked, setPicked] = useState<string[]>([]);
  const [other, setOther] = useState<string | null>(null); // your own words, while answering with Other
  const [sending, setSending] = useState(false);
  const answered = block.answer !== undefined;
  const chosen = new Set(answered ? choices.filter((c) => picks(block.answer!, c.label)).map((c) => c.label) : picked);
  const send = (text: string) => {
    if (!text.trim() || sending) return;
    setSending(true);
    crew.send(block.taskId, text.trim()).catch(() => setSending(false));
  };
  // Several picked: one a line, with your own words last.
  const several = () => send([...picked.map((p) => `- ${p}`), other?.trim() ? `- ${other.trim()}` : ""].filter(Boolean).join("\n"));
  const ready = picked.length > 0 || Boolean(other?.trim());

  return (
    <div className={`ask ${answered ? "answered" : ""}`}>
      <div className="ask-head">
        <Icon name={answered ? "check" : "question"} size={15} />
        {answered ? "Answered" : "Needs your answer"}
      </div>
      <div className="ask-text"><Markdown text={block.question} onSelect={onOpen} /></div>
      {choices.length > 0 && (
        <div className="ask-choices">
          {choices.map((c) => (
            <button
              key={c.label}
              className={`ask-choice ${chosen.has(c.label) ? "picked" : ""}`}
              disabled={answered || sending}
              aria-pressed={chosen.has(c.label)}
              onClick={() => (block.multiple ? setPicked(chosen.has(c.label) ? picked.filter((p) => p !== c.label) : [...picked, c.label]) : send(c.label))}
            >
              <span className={`ask-mark ${block.multiple ? "box" : ""}`}>{chosen.has(c.label) && <Icon name="check" size={11} />}</span>
              <span className="ask-choice-text">
                <span className="ask-label"><Marked text={c.label} /></span>
                {c.description && <span className="ask-description"><Marked text={c.description} /></span>}
              </span>
            </button>
          ))}
          {!answered && other === null && (
            <button className="ask-choice" disabled={sending} onClick={() => setOther("")}>
              <span className={`ask-mark ${block.multiple ? "box" : ""}`} />
              <span className="ask-choice-text">
                <span className="ask-label">Other</span>
                <span className="ask-description">Answer in your own words</span>
              </span>
            </button>
          )}
          {!answered && other !== null && (
            <div className="ask-other">
              <input
                autoFocus
                value={other}
                placeholder="Your answer"
                disabled={sending}
                onChange={(e) => setOther(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.nativeEvent.isComposing) (block.multiple ? several() : send(other));
                  else if (e.key === "Escape" && !e.nativeEvent.isComposing) setOther(null);
                }}
              />
              {!block.multiple && (
                <button className="button small" disabled={!other.trim() || sending} onClick={() => send(other)}>
                  Send
                </button>
              )}
            </div>
          )}
          {!answered && block.multiple && (
            <button className="button small ask-send" disabled={!ready || sending} onClick={several}>
              Send
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Whether an answer picked this choice: it's the answer, or one of its lines. */
function picks(answer: string, label: string): boolean {
  const wanted = label.trim().toLowerCase();
  return answer.split("\n").some((line) => line.replace(/^\s*[-*•]\s*/, "").trim().toLowerCase() === wanted);
}
