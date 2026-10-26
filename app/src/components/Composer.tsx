import { useEffect, useRef, useState } from "react";
import { bridge } from "../api";
import type { Bot } from "../types";
import { labelOf } from "./Avatar";
import { Icon } from "./Icon";
import { UsagePanel } from "./UsagePanel";

// Commands run in the app itself, from the message box: nothing is sent to a bot.
const COMMANDS: { name: string; icon: "chart"; description: string }[] = [
  { name: "usage", icon: "chart", description: "What's left on your Claude and Codex plans" },
];

/** The one place you type, or talk: the mic dictates into it. In a group chat, typing @ suggests who to mention, and
 * anywhere, typing / offers the app's own commands. */
export function Composer(props: {
  placeholder: string;
  onSend: (text: string) => Promise<unknown>;
  asking?: boolean;
  autoFocus?: boolean;
  mentions?: string[];
  bots?: Bot[];
  bot?: string;
  onBot?: (name: string) => void;
  workspace?: string;
  onWorkspace?: (path: string) => void;
}) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [listening, setListening] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const before = useRef(""); // what was typed before dictation started; what's heard goes after it

  const typing = /(?:^|\s)@([\w-]*)$/.exec(text)?.[1];
  const suggestions = typing === undefined ? [] : (props.mentions ?? []).filter((m) => m.toLowerCase().startsWith(typing.toLowerCase()));
  const mention = (name: string) => {
    setText(text.replace(/@([\w-]*)$/, `@${name} `));
    area.current?.focus();
  };

  // "/" and a command's start, nothing else: Tab or Enter runs the one picked, instead of sending.
  const slash = /^\/(\S*)$/.exec(text)?.[1];
  const commands = slash === undefined ? [] : COMMANDS.filter((c) => c.name.startsWith(slash.toLowerCase()));
  const [pick, setPick] = useState(0);
  const [panel, setPanel] = useState<string | null>(null);
  const run = (name: string) => {
    setText("");
    setPick(0);
    setPanel(name);
    area.current?.focus();
  };

  const [micError, setMicError] = useState<string | null>(null);
  useEffect(() => {
    const offs = [
      bridge.dictation.onHeard((heard) => setText(joined(before.current, heard))),
      bridge.dictation.onStopped((error) => {
        setListening(false);
        setMicError(error);
      }),
    ];
    return () => offs.forEach((off) => off());
  }, []);
  const dictate = async () => {
    if (listening) {
      setListening(false);
      await bridge.dictation.stop();
      area.current?.focus();
      return;
    }
    before.current = text;
    setMicError(null);
    try {
      setListening(true);
      await bridge.dictation.start();
    } catch (e) {
      setListening(false);
      setMicError(String(e).replace(/^Error: /, ""));
    }
  };

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
    if (listening) {
      setListening(false);
      bridge.dictation.stop();
    }
    setSending(true);
    setText("");
    setPanel(null);
    try {
      await props.onSend(value);
    } catch {
      setText(value);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className={`composer ${props.asking ? "asking" : ""} ${listening ? "listening" : ""}`} onClick={() => area.current?.focus()}>
      {panel === "usage" && <UsagePanel onClose={() => setPanel(null)} />}
      {commands.length > 0 && (
        <div className="commands" role="listbox">
          {commands.map((c, i) => (
            <button
              key={c.name}
              role="option"
              aria-selected={i === Math.min(pick, commands.length - 1)}
              className={`command-option ${i === Math.min(pick, commands.length - 1) ? "on" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setPick(i)}
              onClick={() => run(c.name)}
            >
              <Icon name={c.icon} size={15} />
              <span className="command-name">/{c.name}</span>
              <span className="command-desc">{c.description}</span>
            </button>
          ))}
        </div>
      )}
      {suggestions.length > 0 && (
        <div className="mentions">
          {suggestions.map((name) => (
            <button key={name} className="mention-option" onMouseDown={(e) => e.preventDefault()} onClick={() => mention(name)}>
              {labelOf(name)} <span className="faint">@{name}</span>
            </button>
          ))}
        </div>
      )}
      <textarea
        ref={area}
        rows={1}
        value={text}
        placeholder={props.placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (commands.length > 0 && (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey))) {
            e.preventDefault();
            run(commands[Math.min(pick, commands.length - 1)].name);
          } else if (commands.length > 0 && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            setPick((Math.min(pick, commands.length - 1) + (e.key === "ArrowDown" ? 1 : commands.length - 1)) % commands.length);
          } else if (e.key === "Escape" && (commands.length > 0 || panel)) {
            e.preventDefault();
            if (commands.length > 0) setText("");
            else setPanel(null);
          } else if ((e.key === "Tab" || e.key === "Enter") && suggestions.length > 0) {
            e.preventDefault();
            mention(suggestions[0]);
          } else if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
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
        {listening ? (
          <span className="composer-hint listening-hint">Listening…</span>
        ) : micError ? (
          <span className="composer-hint listening-hint" title={micError}>
            {micError.length > 60 ? `${micError.slice(0, 59)}…` : micError}
          </span>
        ) : (
          <span className="composer-hint faint">/ for commands · ⇧↩ new line</span>
        )}
        <button
          className={`mic ${listening ? "on" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            dictate();
          }}
          title={listening ? "Stop dictating" : "Dictate"}
        >
          <Icon name="mic" size={15} />
        </button>
        <button className="send" disabled={!text.trim() || sending} onClick={send} title="Send">
          <Icon name="arrowUp" size={15} />
        </button>
      </div>
    </div>
  );
}

function joined(typed: string, heard: string): string {
  if (!heard) return typed;
  return typed && !/\s$/.test(typed) ? `${typed} ${heard}` : typed + heard;
}
