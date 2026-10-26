// Small pieces the pages share: a page frame, a side sheet for editing, a switch, and a labelled field.

import type { ReactNode } from "react";
import { Icon } from "./Icon";

export function Page(props: { title: string; actions?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <>
      <header className="page-head" data-tauri-drag-region>
        <h1 data-tauri-drag-region>{props.title}</h1>
        <span className="grow" data-tauri-drag-region />
        {props.actions}
      </header>
      <div className="page-scroll">
        <div className={`page-col ${props.wide ? "wide" : ""}`}>{props.children}</div>
      </div>
    </>
  );
}

/** Editing happens in a sheet over the page's right side, so the list stays in view. */
export function Sheet(props: { title: string; onClose: () => void; children: ReactNode; footer: ReactNode }) {
  return (
    <div className="sheet-backdrop" onMouseDown={props.onClose}>
      <aside className="sheet" onMouseDown={(e) => e.stopPropagation()}>
        <header className="sheet-head">
          <h2>{props.title}</h2>
          <button className="icon-button" onClick={props.onClose} title="Close">
            <Icon name="x" size={15} />
          </button>
        </header>
        <div className="sheet-body">{props.children}</div>
        <footer className="sheet-foot">{props.footer}</footer>
      </aside>
    </div>
  );
}

export function Switch(props: { on: boolean; onChange: (on: boolean) => void; label?: string; disabled?: boolean }) {
  return (
    <button
      role="switch"
      aria-checked={props.on}
      aria-label={props.label}
      className={`switch ${props.on ? "on" : ""}`}
      disabled={props.disabled}
      onClick={(e) => {
        e.stopPropagation();
        props.onChange(!props.on);
      }}
    >
      <span />
    </button>
  );
}

export function Field(props: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{props.label}</span>
      {props.children}
      {props.hint && <span className="field-hint">{props.hint}</span>}
    </label>
  );
}

export function Segmented<T extends string>(props: { value: T; options: [T, string][]; onChange: (value: T) => void }) {
  return (
    <span className="segmented inline">
      {props.options.map(([value, label]) => (
        <button key={value} type="button" className={props.value === value ? "on" : ""} onClick={() => props.onChange(value)}>
          {label}
        </button>
      ))}
    </span>
  );
}

/** For a toggle's onClick: drops its hover highlight until the pointer leaves, so it doesn't look still pressed once
 * what it toggles has moved (the system only rechecks hover when the pointer moves). */
export function settle(e: React.MouseEvent<HTMLElement>) {
  const button = e.currentTarget;
  button.classList.add("settled");
  button.blur();
  button.addEventListener("pointerleave", () => button.classList.remove("settled"), { once: true });
}
