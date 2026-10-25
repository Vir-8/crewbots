import type { Bot, Status } from "../types";

const HUES = [212, 262, 330, 24, 150, 190, 45, 290];

function hue(name: string): number {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return HUES[h % HUES.length];
}

/** A bot's face: its initial on its own colour, with a ring that pulses while it works. */
export function Avatar(props: { name: string; bot?: Bot; status?: Status; size?: number; title?: string }) {
  const size = props.size ?? 28;
  const working = props.status === "running" || props.status === "queued";
  return (
    <span
      className={`avatar ${working ? "working" : ""} ${props.status === "needs_input" ? "asking" : ""}`}
      style={{ width: size, height: size, fontSize: size * 0.38, ["--hue" as string]: hue(props.name) }}
      title={props.title ?? (props.bot ? `${props.bot.name} · ${props.bot.title} · ${props.bot.backend}` : props.name)}
    >
      {props.name === "user" ? "You" : props.name.slice(0, 1).toUpperCase() + props.name.slice(1, 2)}
    </span>
  );
}
