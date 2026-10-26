import { useState } from "react";
import { Icon } from "../../components/Icon";
import { ago } from "../../components/bits";
import type { PullSummary } from "../../types";
import type { Selected } from "./shared";

/** One list of pull requests. `pulls` is null until it's been asked for: `onOpen` asks, the first time it's opened. */
export function PullGroup(props: {
  title: string;
  pulls: PullSummary[] | null;
  selected: Selected | null;
  onSelect: (s: Selected) => void;
  empty?: string;
  collapsed?: boolean;
  onOpen?: () => void;
}) {
  const [open, setOpen] = useState(!props.collapsed);
  const pulls = props.pulls ?? [];
  if (props.pulls && pulls.length === 0 && !props.empty) return null;
  return (
    <section className="pull-group">
      <button
        className="pull-group-head"
        onClick={() => {
          if (!open) props.onOpen?.();
          setOpen(!open);
        }}
      >
        <Icon name={open ? "chevronDown" : "chevronRight"} size={12} />
        {props.title}
        {props.pulls && <span className="faint">{pulls.length}</span>}
      </button>
      {open && !props.pulls && <p className="faint small pad-x">Asking GitHub…</p>}
      {open && props.pulls && pulls.length === 0 && <p className="faint small pad-x">{props.empty}</p>}
      {open &&
        pulls.map((p) => {
          const on = props.selected?.repo === p.repo && props.selected?.number === p.number;
          return (
            <button key={`${p.repo}#${p.number}`} className={`pull-row ${on ? "on" : ""}`} onClick={() => props.onSelect({ repo: p.repo, number: p.number })}>
              <span className="pull-row-title">
                {p.draft && <span className="draft-tag">Draft</span>}
                {p.title}
              </span>
              <span className="pull-row-meta">
                {p.author_avatar ? <img className="gh-avatar" src={p.author_avatar} alt="" /> : <span className="gh-avatar" />}
                <span className="faint">
                  {p.author} · {p.repo.split("/")[1]}#{p.number} · {ago(p.updated_at ?? p.created_at ?? "", true)}
                </span>
                <span className="grow" />
                {p.reviewed_at && <span className="reviewed-dot" title="You have a review of this" />}
                {(p.checks === "FAILURE" || p.checks === "ERROR") && <Icon name="x" size={13} className="check-failed" />}
              </span>
            </button>
          );
        })}
    </section>
  );
}
