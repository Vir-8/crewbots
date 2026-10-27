// A few line icons, drawn on a 16px grid with the text colour, so they sit quietly next to text.

const PATHS = {
  plus: "M8 3.5v9M3.5 8h9",
  arrowUp: "M8 13V3.5M4 7.5 8 3.5l4 4",
  chevronRight: "m6 3.5 4.5 4.5L6 12.5",
  chevronDown: "m3.5 6 4.5 4.5L12.5 6",
  check: "m3.5 8.5 3 3 6-7",
  x: "m4.5 4.5 7 7m0-7-7 7",
  merge: "M5 3.5v9M5 6.5c0 2.5 2 3.5 6 3.5M11 10a1.5 1.5 0 1 0 0 .01M5 3.5a1.5 1.5 0 1 0 0 .01M5 12.5a1.5 1.5 0 1 0 0 .01",
  folder: "M2.5 4.5a1 1 0 0 1 1-1h3l1.5 1.5h4.5a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1z",
  branch: "M5 3v10M11 3v3.5a3 3 0 0 1-3 3H5",
  question: "M6.2 6a1.9 1.9 0 0 1 3.7.6c0 1.3-1.9 1.6-1.9 2.9M8 11.7v.01M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13z",
  alert: "M8 5v3.5M8 11v.01M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13z",
  diff: "M8 3v5M5.5 5.5h5M5.5 12h5",
  panelClose: "M3 3.5h10v9H3zM9.5 3.5v9",
  sidebar: "M2.5 3.5h11v9h-11zM6.5 3.5v9",
  compose: "M11 2.5l2.5 2.5L6 12.5H3.5V10zM9.5 4l2.5 2.5",
  pause: "M6 4.5v7M10 4.5v7",
  message: "M3 4.5a1.5 1.5 0 0 1 1.5-1.5h7A1.5 1.5 0 0 1 13 4.5v5a1.5 1.5 0 0 1-1.5 1.5H7l-3 2.5V11h0a1 1 0 0 1-1-1z",
  mic: "M8 2.5a2 2 0 0 1 2 2v3.5a2 2 0 0 1-4 0V4.5a2 2 0 0 1 2-2zM4.5 7.5a3.5 3.5 0 0 0 7 0M8 11v2.5",
  search: "M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM10.5 10.5 13.5 13.5",
  settings: "M8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4",
  profile: "M3 3.5h10v9H3zM9 3.5v9",
  users: "M6 7.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM1.5 13.5c0-2.2 2-3.5 4.5-3.5s4.5 1.3 4.5 3.5M10.5 2.7a2.3 2.3 0 0 1 0 4.6M12 10.2c1.5.4 2.5 1.5 2.5 3.3",
  back: "M10 3.5 5.5 8l4.5 4.5",
  chart: "M3 13.5h10M4.5 11V7.5M8 11V4M11.5 11V9",
  file: "M4.5 2.5h4.5l2.5 2.5v8.5h-7zM9 2.5V5h2.5",
  tool: "M10.5 2.5a3 3 0 0 0-2.8 4L3.3 10.9a1.3 1.3 0 0 0 1.8 1.8l4.4-4.4a3 3 0 0 0 4-2.8l-1.8 1.8-1.7-.4-.4-1.7z",
  external: "M9 3.5h3.5V7M12.5 3.5 7.5 8.5M11 9.5v2.5a1 1 0 0 1-1 1H4.5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1H7",
  archive: "M2.5 3.5h11v3h-11zM3.5 6.5v6h9v-6M6.5 9h3",
  handoff: "M2.5 5.5h9M9 3l2.5 2.5L9 8M13.5 10.5h-9M7 8l-2.5 2.5L7 13",
  bellOff: "M4.5 11V7a3.5 3.5 0 0 1 5.6-2.8M11.5 7.5V11l1 1h-9M6.8 13.5h2.4M2.5 2.5l11 11",
  play: "M5.5 3.8v8.4L12 8z",
  copy: "M6.5 5.5h6a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1h-6a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1zM3.5 10.5v-6a1 1 0 0 1 1-1h6",
  repeat: "M3 7.5V7a3 3 0 0 1 3-3h6.5M10.5 2l2 2-2 2M13 8.5V9a3 3 0 0 1-3 3H3.5M5.5 14l-2-2 2-2",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={`icon ${className ?? ""}`}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
