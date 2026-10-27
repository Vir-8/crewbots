import { useEffect, useState } from "react";
import type { Crew } from "../state";

/** What the message box's commands can do here: each is there only where it makes sense (a thread to stop, a
 * crew to pause). */
export type Actions = {
  who?: string; // the bot this box talks to, as the previews name it
  new?: (goal: string) => Promise<unknown>;
  group?: (title: string) => void;
  remember?: (note: string) => Promise<unknown>;
  stop?: () => Promise<unknown>;
  pause?: () => Promise<unknown>;
  resume?: () => Promise<unknown>;
};

/** /pause or /resume, whichever the crew can do now. */
export const crewCommands = (crew: Crew): Actions =>
  crew.crewState?.paused ? { resume: () => crew.pause(false) } : { pause: () => crew.pause(true) };

type Said = { title: string; text: string; button: string; needs?: string };

function said(name: string, arg: string, who: string): Said {
  switch (name) {
    case "new":
      return { title: "New thread", text: `${who} starts a thread on: “${arg}”. It works beside this chat, and its result comes back here.`, button: "Start it", needs: "what it's for" };
    case "group":
      return { title: "New group chat", text: `A group chat${arg ? ` called “${arg}”` : ""} with ${who}. You choose who else is in it next.`, button: "Choose who's in it" };
    case "remember":
      return { title: `A note for ${who}`, text: `“${arg}”. It's kept in ${who}'s memory, and reaches its next turn.`, button: "Keep it", needs: "the note" };
    case "stop":
      return { title: "Stop", text: "Stops what's running here now. A message carries it on again.", button: "Stop it" };
    case "pause":
      return { title: "Pause the crew", text: "No bot starts a new turn until you resume. Turns already running finish.", button: "Pause" };
    default:
      return { title: "Resume the crew", text: "Bots start turns again, and what waited goes ahead.", button: "Resume" };
  }
}

/** A command, shown before it runs: what it will do, and the button that does it, or ↩ in the box (each ↩ bumps
 * `confirmed`). Nothing runs until then. */
export function CommandPreview(props: { name: string; arg: string; actions: Actions; confirmed: number; onDone: () => void; onCancel: () => void }) {
  const { name, arg, actions } = props;
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const s = said(name, arg, actions.who ?? "this bot");
  const missing = Boolean(s.needs && !arg);
  const go = async () => {
    const act = { new: () => actions.new?.(arg), group: () => actions.group?.(arg), remember: () => actions.remember?.(arg), stop: actions.stop, pause: actions.pause, resume: actions.resume }[name];
    if (!act || missing || busy) return;
    setBusy(true);
    setError(null);
    try {
      await act();
      props.onDone();
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (props.confirmed) go();
  }, [props.confirmed]);
  return (
    <div className="command-panel command-preview" onClick={(e) => e.stopPropagation()}>
      <div className="command-panel-head">
        <span className="command-panel-title">{s.title}</span>
        <span className="grow" />
        <button className="text-button" onClick={props.onCancel}>
          Cancel
        </button>
        <button className="button small primary" disabled={missing || busy} onClick={go}>
          {s.button}
        </button>
      </div>
      <p className="command-preview-text">{missing ? `Type ${s.needs} after /${name}.` : s.text}</p>
      {error && <p className="command-preview-text bad">{error}</p>}
    </div>
  );
}
