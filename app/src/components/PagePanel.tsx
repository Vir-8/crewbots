import { type ReactNode, useEffect, useState } from "react";
import { api, bridge } from "../api";
import type { PageOut } from "../api-types";
import type { Crew } from "../state";
import { Avatar, labelOf } from "./Avatar";
import { Markdown, PageOpener, ago } from "./bits";
import { Icon } from "./Icon";
import "../styles/panels.css";

/** One of a bot's pages, beside its chat: its scratchpad, or a page that grew from it. Its [[links]] open the pages
 * they name, here; you can edit it, and the bot sees your change in its next turn. A page the bot changes while it's
 * open is read again, unless you're in the middle of editing it. */
export function PagePanel(props: { crew: Crew; bot: string; page: string; onPage: (page: string) => void; onOpen: (id: string) => void; onClose: () => void; edge?: ReactNode }) {
  const { crew, bot } = props;
  const [page, setPage] = useState<PageOut | null>(null);
  const [missing, setMissing] = useState(false);
  const [draft, setDraft] = useState<string | null>(null); // editing, while not null
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trail, setTrail] = useState<string[]>([]); // pages followed here by their links, for going back
  const stamp = crew.pageStamp(bot);

  useEffect(() => {
    setDraft(null);
    setError(null);
  }, [bot, props.page]);
  useEffect(() => {
    if (draft !== null) return; // what you're writing isn't replaced under you
    api.page(bot, props.page).then(
      (p) => {
        setPage(p);
        setMissing(false);
      },
      () => {
        setPage(null);
        setMissing(true);
      },
    );
  }, [bot, props.page, stamp, draft === null]);

  const follow = (name: string) => {
    setTrail([...trail, page?.name ?? props.page]);
    props.onPage(name);
  };
  const back = () => {
    const previous = trail.at(-1);
    if (!previous) return;
    setTrail(trail.slice(0, -1));
    props.onPage(previous);
  };
  const save = async () => {
    if (draft === null) return;
    setSaving(true);
    try {
      setPage(await api.savePage(bot, page?.name ?? props.page, draft));
      setMissing(false);
      setDraft(null);
      setError(null);
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    } finally {
      setSaving(false);
    }
  };
  const name = page?.name ?? props.page;

  return (
    <aside className="panel page-panel">
      {props.edge}
      <div className="panel-drag" data-tauri-drag-region />
      <header className="page-head">
        {trail.length > 0 && (
          <button className="icon-button" onClick={back} title={`Back to ${trail.at(-1)}`}>
            <Icon name="back" size={15} />
          </button>
        )}
        <Avatar name={bot} size={20} />
        <div className="page-title">
          <span className="faint">{labelOf(bot)} /</span> {name}
        </div>
        <span className="grow" />
        {draft === null ? (
          <>
            <button className="icon-button" onClick={() => setDraft(page?.text ?? "")} disabled={!page && !missing} title="Edit this page">
              <Icon name="compose" size={15} />
            </button>
            {page && (
              <button className="icon-button" onClick={() => bridge.reveal(page.path)} title="Show it in Finder">
                <Icon name="folder" size={15} />
              </button>
            )}
          </>
        ) : (
          <>
            <button className="button small" onClick={() => setDraft(null)} disabled={saving}>
              Cancel
            </button>
            <button className="button small primary" onClick={save} disabled={saving}>
              {saving ? "Saving…" : "Save"}
            </button>
          </>
        )}
        <button className="icon-button" onClick={props.onClose} title="Close">
          <Icon name="x" size={15} />
        </button>
      </header>
      <div className="page-body">
        {error && <p className="bad small">{error}</p>}
        {draft !== null ? (
          <textarea
            className="page-editor"
            value={draft}
            autoFocus
            spellCheck={false}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "s") {
                e.preventDefault();
                void save();
              } else if (e.key === "Escape") setDraft(null);
            }}
          />
        ) : missing ? (
          <div className="page-empty faint">
            {name === "Scratchpad"
              ? `${labelOf(bot)} hasn't started its scratchpad yet. It keeps what it's tracking there: what's top of mind, what's in flight, and what waits on you.`
              : "There's no page by this name yet."}
          </div>
        ) : page ? (
          <PageOpener.Provider value={follow}>
            <div className="page-text">
              <Markdown text={page.text ?? ""} onSelect={props.onOpen} />
            </div>
            <div className="page-updated faint">Updated {ago(page.updated)}</div>
          </PageOpener.Provider>
        ) : (
          <div className="thread-loading" />
        )}
      </div>
    </aside>
  );
}
