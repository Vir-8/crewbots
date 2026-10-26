import { type TextareaHTMLAttributes, useRef, useState } from "react";

type Props = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "value" | "onChange"> & {
  value: string;
  onChange: (value: string) => void;
  people: string[]; // the pull request's author first
};

/** A textarea that offers the pull request's people as you type an @mention: ↑↓ to choose, ↩ or ⇥ to put one in,
 * esc to type on. Anything else it hands to the textarea's own keys. */
export function MentionArea({ value, onChange, people, onKeyDown, onBlur, ...rest }: Props) {
  const box = useRef<HTMLTextAreaElement>(null);
  const [at, setAt] = useState<{ start: number; query: string } | null>(null);
  const [pick, setPick] = useState(0);
  const query = at?.query.toLowerCase() ?? "";
  const matches = at
    ? people
        .filter((p) => p.toLowerCase().includes(query))
        .sort((a, b) => Number(!a.toLowerCase().startsWith(query)) - Number(!b.toLowerCase().startsWith(query)))
        .slice(0, 6)
    : [];
  const open = matches.length > 0 && !(matches.length === 1 && matches[0].toLowerCase() === query);

  const look = (text: string, caret: number) => {
    const typed = /(^|[^\w`@])@([\w-]*)$/.exec(text.slice(0, caret));
    const next = typed ? { start: caret - typed[2].length - 1, query: typed[2] } : null;
    if (next?.start !== at?.start || next?.query !== at?.query) setPick(0);
    setAt(next);
  };
  const choose = (login: string) => {
    if (!at) return;
    const caret = at.start + login.length + 2;
    onChange(`${value.slice(0, at.start)}@${login} ${value.slice(at.start + 1 + at.query.length)}`);
    setAt(null);
    requestAnimationFrame(() => box.current?.setSelectionRange(caret, caret));
  };

  return (
    <div className="mention-area">
      <textarea
        ref={box}
        {...rest}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          look(e.target.value, e.target.selectionStart);
        }}
        onClick={(e) => look(e.currentTarget.value, e.currentTarget.selectionStart)}
        onKeyUp={(e) => ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key) && look(e.currentTarget.value, e.currentTarget.selectionStart)}
        onBlur={(e) => {
          setAt(null);
          onBlur?.(e);
        }}
        onKeyDown={(e) => {
          if (open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            setPick((p) => (p + (e.key === "ArrowDown" ? 1 : -1) + matches.length) % matches.length);
          } else if (open && ((e.key === "Enter" && !e.metaKey && !e.ctrlKey && !e.shiftKey) || e.key === "Tab")) {
            e.preventDefault();
            choose(matches[pick] ?? matches[0]);
          } else if (open && e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            setAt(null);
          } else {
            onKeyDown?.(e);
          }
        }}
      />
      {open && (
        <div className="mention-menu" role="listbox">
          {matches.map((login, i) => (
            <button
              key={login}
              type="button"
              role="option"
              aria-selected={i === pick}
              className={i === pick ? "on" : ""}
              onMouseDown={(e) => {
                e.preventDefault(); // keeps the textarea's focus
                choose(login);
              }}
              onMouseEnter={() => setPick(i)}
            >
              <span>@{login}</span>
              {login === people[0] && <span className="faint">author</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
