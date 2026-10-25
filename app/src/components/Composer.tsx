import { useEffect, useRef, useState } from "react";
import { bridge } from "../api";
import type { Bot } from "../types";
import { Icon } from "./Icon";

/** The one place you type. In a conversation it replies to that task; for a new goal it also picks who and where. */
export function Composer(props: {
  placeholder: string;
  onSend: (text: string) => Promise<unknown>;
  asking?: boolean;
  autoFocus?: boolean;
  bots?: Bot[];
  bot?: string;
  onBot?: (name: string) => void;
  workspace?: string;
  onWorkspace?: (path: string) => void;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);
  useEffect(() => {
    if (props.autoFocus) area.current?.focus();
  }, [props.autoFocus]);

  const send = async () => {
    const value = text.trim();
    if (!value || sending) return;
    setSending(true);
    setText("");
    try {
      await props.onSend(value);
    } catch {
      setText(value);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className={`composer ${props.asking ? "asking" : ""}`} onClick={() => area.current?.focus()}>
      <textarea
        ref={area}
        rows={1}
        value={text}
        placeholder={props.placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
      />
      <div className="composer-bar">
        {props.bots && props.onBot && (
          <label className="chip select" onClick={(e) => e.stopPropagation()}>
            <span className="faint">To</span>
            <select value={props.bot} onChange={(e) => props.onBot!(e.target.value)}>
              {props.bots.map((b) => (
                <option key={b.name} value={b.name}>
                  {b.name}
                </option>
              ))}
            </select>
            <Icon name="chevronDown" size={12} />
          </label>
        )}
        {props.onWorkspace && (
          <button
            className="chip"
            onClick={async (e) => {
              e.stopPropagation();
              const picked = await bridge.pickFolder();
              if (picked) props.onWorkspace!(picked);
            }}
            title={props.workspace || "Choose the folder or repo to work in"}
          >
            <Icon name="folder" size={13} />
            {props.workspace ? props.workspace.split("/").filter(Boolean).at(-1) : "Choose a folder"}
          </button>
        )}
        <span className="grow" />
        <span className="composer-hint faint">⇧↩ new line</span>
        <button className="send" disabled={!text.trim() || sending} onClick={send} title="Send">
          <Icon name="arrowUp" size={15} />
        </button>
      </div>
    </div>
  );
}
