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
  compose: "M11 2.5l2.5 2.5L6 12.5H3.5V10zM9.5 4l2.5 2.5",
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
