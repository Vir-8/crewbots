// Each bot's face: a soft shape in its own color, with two eyes. The shape comes from the bot's name, so a bot always
// looks the same; the color comes from its place in the crew, so no two bots in a crew share one.

import type { ReactElement } from "react";

const COLORS = ["#8b7cf6", "#2bb58f", "#f06a4e", "#e2558f", "#4c8df6", "#f2a93b", "#59b85c", "#e5484d", "#38b2c9"];

const SHAPES: ((color: string) => ReactElement)[] = [
  (c) => <circle cx="16" cy="16" r="13" fill={c} />,
  (c) => <path d="M16 2.5c0 0-11 11.5-11 18a11 11 0 0 0 22 0c0-6.5-11-18-11-18z" fill={c} />,
  (c) => <rect x="3.5" y="3.5" width="25" height="25" rx="8" fill={c} />,
  (c) => (
    <g fill={c}>
      {[0, 60, 120, 180, 240, 300].map((a) => (
        <circle key={a} cx={16 + 7.5 * Math.cos((a * Math.PI) / 180)} cy={16 + 7.5 * Math.sin((a * Math.PI) / 180)} r="6.5" />
      ))}
      <circle cx="16" cy="16" r="8" />
    </g>
  ),
  (c) => <path d="M16 3.5 27 9.8v12.4L16 28.5 5 22.2V9.8z" fill={c} stroke={c} strokeWidth="3" strokeLinejoin="round" />,
];

let crew: string[] = [];

/** Called whenever the crew is (re)loaded, so colors follow the crew's own order. */
export function setCrew(names: string[]) {
  crew = [...names].sort();
}

function hash(text: string): number {
  let h = 2166136261;
  for (const ch of text) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return Math.abs(h);
}

export function Avatar({ name, size = 32, working }: { name: string; size?: number; working?: boolean }) {
  const h = hash(name);
  const place = crew.indexOf(name);
  const color = COLORS[(place >= 0 ? place : h) % COLORS.length];
  const shape = SHAPES[Math.floor(h / COLORS.length) % SHAPES.length];
  return (
    <span className="avatar" style={{ width: size, height: size }}>
      <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true">
        {shape(color)}
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
