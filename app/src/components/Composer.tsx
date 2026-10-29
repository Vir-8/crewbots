import { useEffect, useRef, useState } from "react";
import { bridge } from "../api";
import { readDraft, writeDraft } from "../drafts";
import { argumentOf, commandsFor, finishMention, mentionTyped, pickerKey } from "../typing";
import { type Actions, CommandPreview } from "./CommandPreview";
import type { Bot } from "../types";
import { labelOf } from "./Avatar";
import { Icon } from "./Icon";
import { type Pending, PendingFiles, upload } from "./Attachments";
import { StatusPanel } from "./StatusPanel";
import { UsagePanel } from "./UsagePanel";
import "../styles/composer.css";

/** The one place you type, or talk: @ suggests who to mention, / offers commands, and the mic dictates into it. */
/** Asks the open chat's message box to show one of its panels, as typing its command there would. */
export const PANEL_EVENT = "crewbots:panel";
// The mic is one for the whole app: what's heard goes into the box whose mic is on, of all the boxes open (a chat's,
// and a thread's beside it). Turning on another's tells the one before to stop listening.
const DICTATING_EVENT = "crewbots:dictating";
let dictating: object | null = null;
export const showPanel = (name: string) => window.dispatchEvent(new CustomEvent(PANEL_EVENT, { detail: name }));

export function Composer(props: {
  placeholder: string;
  onSend: (text: string, attachments: string[]) => Promise<unknown>;
  asking?: boolean;
  autoFocus?: boolean;
  mentions?: string[];
  bots?: Bot[];
  bot?: string;
  onBot?: (name: string) => void;
  workspace?: string;
  onWorkspace?: (path: string) => void;
  task?: string; // the chat or thread this box talks to, for /status
  draft?: string; // where what's typed but not sent is kept, so a reload or relaunch loses nothing
  main?: boolean; // the open chat's box: it shows what the command palette asks for (Usage, Status)
  actions?: Actions; // what its commands can do here: /new, /group, /remember, /stop, /pause, /resume
}) {
  const kept = useState(() => readDraft<Draft>(props.draft))[0];
  const [text, setText] = useState(kept?.text ?? "");
  const [sending, setSending] = useState(false);
  // Images and files to send with it: picked with +, pasted, or dropped on the box.
  const [files, setFiles] = useState<Pending[]>(kept?.files ?? []);
  useEffect(() => {
    // Only files that finished uploading are kept: they're in the crew's folder, and found again by their id.
    const uploaded = files.filter((f) => f.id).map(({ key, name, kind, size, id, path }) => ({ key, name, kind, size, id, path }));
    writeDraft(props.draft, { text, files: uploaded }, !text.trim() && !uploaded.length);
  }, [props.draft, text, files]);
  const [dropping, setDropping] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const attach = (list: FileList | File[] | null) => {
    if (!list || !list.length) return;
    upload(
      [...list],
      (key, fields) => setFiles((fs) => fs.map((f) => (f.key === key ? { ...f, ...fields } : f))),
      (p) => setFiles((fs) => [...fs, p]),
    );
    area.current?.focus();
  };
  const uploading = files.some((f) => !f.id && !f.error);
  const ready = files.filter((f) => f.id);
  const [listening, setListening] = useState(false);
  const me = useRef({}).current; // this box, for the mic to tell it from the others
  const area = useRef<HTMLTextAreaElement>(null);
  const before = useRef(""); // what was typed before dictation started; what's heard goes after it

  const [dismissedMention, setDismissedMention] = useState<string | null>(null);
  const typing = mentionTyped(text);
  const suggestions = typing === undefined || dismissedMention === text ? [] : (props.mentions ?? []).filter((m) => m.toLowerCase().startsWith(typing.toLowerCase()));
  const mention = (name: string) => {
    setPick(0);
    setDismissedMention(null);
    setText(finishMention(text, name));
    area.current?.focus();
  };

  // "/" and a command's start (or a whole one and what it takes): Tab or Enter shows the one picked above the box,
  // instead of sending. /usage and /status are shown at once; the others say what they'll do, and ↩ again does it.
  const actions = props.actions ?? {};
  const can = (name: string) => name === "usage" || (name === "status" ? Boolean(props.task) : Boolean(actions[name as keyof Actions]));
  const commands = commandsFor(text, can);
  const [pick, setPick] = useState(0);
  const [panel, setPanel] = useState<string | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(0);
  const run = (name: string) => {
    setPick(0);
    area.current?.focus();
    if (name === "usage" || name === "status") {
      setText("");
      setPanel(name);
    } else {
      setPanel(null);
      setPreview(name);
      setConfirmed(0);
    }
  };
  useEffect(() => {
    if (preview && !text.startsWith(`/${preview}`)) setPreview(null); // typed something else
  }, [text, preview]);
  useEffect(() => {
    if (!props.main) return;
    const asked = (e: Event) => setPanel((e as CustomEvent<string>).detail);
    window.addEventListener(PANEL_EVENT, asked);
    return () => window.removeEventListener(PANEL_EVENT, asked);
  }, [props.main]);

  const [micError, setMicError] = useState<string | null>(null);
  useEffect(() => {
    const taken = (e: Event) => (e as CustomEvent<object>).detail !== me && setListening(false);
    window.addEventListener(DICTATING_EVENT, taken);
    const offs = [
      bridge.dictation.onHeard((heard) => dictating === me && setText(joined(before.current, heard))),
      bridge.dictation.onStopped((error) => {
        if (dictating !== me) return;
        dictating = null;
        setListening(false);
        setMicError(error);
      }),
    ];
    return () => {
      window.removeEventListener(DICTATING_EVENT, taken);
      offs.forEach((off) => off());
      if (dictating === me) dictating = null;
    };
  }, []);
  const dictate = async () => {
    if (listening) {
      setListening(false);
      dictating = null;
      await bridge.dictation.stop();
      area.current?.focus();
      return;
    }
    before.current = text;
    setMicError(null);
    dictating = me;
    window.dispatchEvent(new CustomEvent(DICTATING_EVENT, { detail: me }));
    try {
      setListening(true);
      await bridge.dictation.start();
    } catch (e) {
      setListening(false);
      dictating = null;
      setMicError(String(e).replace(/^Error: /, ""));
    }
  };

  // Its height for what's in it, measured on a hidden copy. Setting the box itself to auto to measure it makes the
  // conversation above it taller for that moment, and WebKit pulls the conversation's scroll back to fit and leaves it
  // there once the box is back to the same height, so each key you type would push the latest messages under it.
  const copy = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => () => copy.current?.remove(), []);
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    const measure = (copy.current ??= document.createElement("textarea"));
    if (!measure.isConnected) {
      measure.setAttribute("aria-hidden", "true");
      measure.tabIndex = -1;
      document.body.append(measure);
    }
    const style = getComputedStyle(el);
    for (const name of ["font-family", "font-size", "font-weight", "font-style", "letter-spacing", "line-height",
      "padding-top", "padding-bottom", "padding-left", "padding-right", "border-left-width", "border-right-width",
      "box-sizing", "tab-size", "white-space", "word-break", "overflow-wrap", "width"])
      measure.style.setProperty(name, style.getPropertyValue(name));
    Object.assign(measure.style, { position: "fixed", top: "0", left: "-10000px", height: "0", overflow: "hidden",
      visibility: "hidden", pointerEvents: "none" });
    measure.value = text;
    el.style.height = `${Math.min(measure.scrollHeight, 240)}px`;
  }, [text]);
  useEffect(() => {
    if (props.autoFocus) area.current?.focus();
  }, [props.autoFocus]);

  const send = async () => {
    const value = text.trim();
    if ((!value && !ready.length) || sending || uploading) return;
    if (listening) {
      setListening(false);
      dictating = null;
      bridge.dictation.stop();
    }
    setSending(true);
    setText("");
    setPanel(null);
    const sent = files;
    setFiles([]);
    try {
      await props.onSend(value, ready.map((f) => f.id!));
    } catch {
      setText(value);
      setFiles(sent);
    } finally {
      setSending(false);
    }
  };

  return (
    <div
      className={`composer ${props.asking ? "asking" : ""} ${listening ? "listening" : ""} ${dropping ? "dropping" : ""}`}
      onClick={() => area.current?.focus()}
      onDragOver={(e) => {
        if (![...e.dataTransfer.types].includes("Files")) return;
        e.preventDefault();
        setDropping(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropping(false);
      }}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return;
        e.preventDefault();
        setDropping(false);
        attach(e.dataTransfer.files);
      }}
    >
      {preview && (
        <CommandPreview
          name={preview}
          arg={argumentOf(text)}
          actions={actions}
          confirmed={confirmed}
          onDone={() => {
            setPreview(null);
            setText("");
          }}
          onCancel={() => setPreview(null)}
        />
      )}
      {panel === "usage" && <UsagePanel onClose={() => setPanel(null)} />}
      {panel === "status" && props.task && <StatusPanel taskId={props.task} onClose={() => setPanel(null)} />}
      {commands.length > 0 && !preview && (
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
        <div className="mentions" role="listbox">
          {suggestions.map((name, i) => (
            <button key={name} role="option" aria-selected={i === Math.min(pick, suggestions.length - 1)} className={`mention-option ${i === Math.min(pick, suggestions.length - 1) ? "on" : ""}`} onMouseDown={(e) => e.preventDefault()} onMouseEnter={() => setPick(i)} onClick={() => mention(name)}>
              {name === "everyone" ? "Everyone" : labelOf(name)} <span className="faint">@{name}</span>
            </button>
          ))}
        </div>
      )}
      {files.length > 0 && <PendingFiles items={files} onRemove={(key) => setFiles((fs) => fs.filter((f) => f.key !== key))} />}
      <textarea
        ref={area}
        onPaste={(e) => {
          const pasted = [...e.clipboardData.files];
          if (!pasted.length) return;
          e.preventDefault();
          attach(pasted);
        }}
        rows={1}
        value={text}
        placeholder={props.placeholder}
        onChange={(e) => {
          if (mentionTyped(text) !== mentionTyped(e.target.value)) setPick(0);
          setDismissedMention(null);
          setText(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return;
          const menu = commands.length ? "commands" : suggestions.length ? "mentions" : null;
          const length = menu === "commands" ? commands.length : suggestions.length;
          const choice = pickerKey(e.key, e.shiftKey, pick, length);
          if (preview && e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            setConfirmed((n) => n + 1);
          } else if (preview && e.key === "Escape") {
            e.preventDefault();
            setPreview(null);
          } else if (choice !== undefined) {
            e.preventDefault();
            if (typeof choice === "number") setPick(choice);
            else if (choice === "choose") {
              if (menu === "commands") run(commands[Math.min(pick, length - 1)].name);
              else mention(suggestions[Math.min(pick, length - 1)]);
            } else if (menu === "commands") setText("");
            else {
              setDismissedMention(text);
              setPick(0);
            }
          } else if (e.key === "Escape" && panel) {
            e.preventDefault();
            setPanel(null);
          } else if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            send();
          }
        }}
      />
      <div className="composer-bar">
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            attach(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          className="icon-button attach"
          onClick={(e) => {
            e.stopPropagation();
            picker.current?.click();
          }}
          title="Attach images or files (or paste, or drop them here)"
        >
          <Icon name="plus" size={16} />
        </button>
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
          <Icon name="mic" size={16} />
        </button>
        <button className="send" disabled={(!text.trim() && !ready.length) || sending || uploading} onClick={send} title={uploading ? "Waiting for attachments to finish" : "Send"}>
          <Icon name="arrowUp" size={16} />
        </button>
      </div>
    </div>
  );
}

type Draft = { text: string; files: Pending[] };

function joined(typed: string, heard: string): string {
  if (!heard) return typed;
  return typed && !/\s$/.test(typed) ? `${typed} ${heard}` : typed + heard;
}
