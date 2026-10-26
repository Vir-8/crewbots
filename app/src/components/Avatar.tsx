// Each bot's face: a soft shape in its own color, with two eyes. You can pick both on its role card; otherwise both
// come from its place in the crew, so bots look different from each other. The colors are the
// charts' series colors too (validated for color-blind readers on the app's dark surface), so a bot looks the same
// everywhere.

import type { ReactElement } from "react";
import type { Bot } from "../types";

export const COLORS: Record<string, string> = {
  blue: "#3987e5",
  orange: "#d95926",
  aqua: "#199e70",
  yellow: "#c98500",
  magenta: "#d55181",
  green: "#008300",
  violet: "#9085e9",
  red: "#e66767",
};
export const COLOR_NAMES = Object.keys(COLORS);
export const SHAPE_NAMES = ["flower", "hex", "drop", "square", "circle"]; // in the order bots get them

const SHAPES: Record<string, (color: string) => ReactElement> = {
  circle: (c) => <circle cx="16" cy="16" r="13" fill={c} />,
  drop: (c) => <path d="M16 2.5c0 0-11 11.5-11 18a11 11 0 0 0 22 0c0-6.5-11-18-11-18z" fill={c} />,
  square: (c) => <rect x="3.5" y="3.5" width="25" height="25" rx="8" fill={c} />,
  flower: (c) => (
    <g fill={c}>
      {[0, 60, 120, 180, 240, 300].map((a) => (
        <circle key={a} cx={16 + 7.5 * Math.cos((a * Math.PI) / 180)} cy={16 + 7.5 * Math.sin((a * Math.PI) / 180)} r="6.5" />
      ))}
      <circle cx="16" cy="16" r="8" />
    </g>
  ),
  hex: (c) => <path d="M16 3.5 27 9.8v12.4L16 28.5 5 22.2V9.8z" fill={c} stroke={c} strokeWidth="3" strokeLinejoin="round" />,
};

let crew: string[] = [];
let chosen = new Map<string, { color?: string; shape?: string }>();
let labels = new Map<string, string>();

/** Called whenever the crew is (re)loaded: its order picks default colors, and each card's own choices win. */
export function setCrew(bots: Bot[]) {
  crew = bots.map((b) => b.name).sort();
  chosen = new Map(bots.map((b) => [b.name, b.avatar ?? {}]));
  labels = new Map(bots.map((b) => [b.name, b.display_name || b.title || b.name]));
}

/** What you call a bot: the name you gave it, else its title. */
export function labelOf(name: string): string {
  return labels.get(name) ?? name;
}

function hash(text: string): number {
  let h = 2166136261;
  for (const ch of text) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return Math.abs(h);
}

/** A bot's color and shape: its own choices, else the defaults. `override` previews an edit before it's saved. */
export function face(name: string, override?: { color?: string | null; shape?: string | null }) {
  const own = { ...chosen.get(name), ...Object.fromEntries(Object.entries(override ?? {}).filter(([, v]) => v)) };
  const place = crew.indexOf(name);
  const color = own.color && COLORS[own.color] ? own.color : COLOR_NAMES[(place >= 0 ? place : hash(name)) % COLOR_NAMES.length];
  const shape = own.shape && SHAPES[own.shape] ? own.shape : SHAPE_NAMES[(place >= 0 ? place : hash(name)) % SHAPE_NAMES.length];
  return { color, shape, hex: COLORS[color] };
}

export function Avatar(props: { name: string; size?: number; working?: boolean; color?: string | null; shape?: string | null }) {
  const { name, size = 32, working } = props;
  const f = face(name, { color: props.color, shape: props.shape });
  return (
    <span className="avatar" style={{ width: size, height: size }}>
      <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true">
        {SHAPES[f.shape](f.hex)}
        <ellipse cx="12.5" cy="16.5" rx="1.6" ry="2.2" fill="#fff" />
        <ellipse cx="19.5" cy="16.5" rx="1.6" ry="2.2" fill="#fff" />
      </svg>
      {working && <span className="avatar-working" title="Working" />}
    </span>
  );
}

/** A group chat's face: its first two bots, overlapped, and how many more. */
export function GroupAvatar({ members, size = 32 }: { members: string[]; size?: number }) {
  const small = Math.round(size * 0.68);
  return (
    <span className="group-avatar" style={{ width: size, height: size }}>
      {members.slice(0, 2).map((m, i) => (
        <span key={m} className={`group-avatar-face f${i}`}>
          <Avatar name={m} size={small} />
        </span>
      ))}
      {members.length > 2 && <span className="group-avatar-more">+{members.length - 2}</span>}
    </span>
  );
}
