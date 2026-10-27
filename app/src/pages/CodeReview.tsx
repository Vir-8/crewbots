import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { Resizer, useWidth } from "../components/Resizer";
import type { Crew } from "../state";
import type { PullSummary } from "../types";
import "../styles/code-review.css";
import { PullGroup } from "./review/PullList";
import { PullView } from "./review/PullView";
import { type Selected, area } from "./review/shared";

// Kept while the app is open, so going back to the teams' list shows it at once while it refreshes.
let keptTeam: PullSummary[] | null = null;

/** Pull requests waiting on you, read through your gh login, and private reviews of them. The reviewers aren't your
 * bots: each is its own agent on a model you choose, and the first leads. Only you submit anything to GitHub. */
export function CodeReview({ crew, onSettings, pick }: { crew: Crew; onSettings: () => void; pick?: Selected | null }) {
  const [selected, setSelected] = useState<Selected | null>(pick ?? null);
  useEffect(() => void (pick && setSelected(pick)), [pick]); // one the command palette went to
  const [refreshing, setRefreshing] = useState(false);
  const inbox = crew.reviewInbox;
  // The kept lists show at once; opening code review asks GitHub for new ones behind them.
  useEffect(() => {
    setRefreshing(true);
    crew.loadReviews(true).finally(() => setRefreshing(false));
  }, []);
  const first = inbox?.assigned?.[0] ?? inbox?.direct?.[0];
  // Your teams' pull requests are often the longest list and the slowest to ask for, so only when you open them.
  const [team, setTeam] = useState<PullSummary[] | null>(keptTeam);
  const [teamError, setTeamError] = useState<string | null>(null);
  const loadTeam = async (fresh = false) => {
    try {
      const answer = await api.reviewInbox(fresh, ["team"]);
      keptTeam = answer.team ?? [];
      setTeam(keptTeam);
      setTeamError(answer.error);
    } catch (e) {
      setTeamError(String(e).replace(/^Error: /, ""));
    }
  };
  useEffect(() => {
    if (!selected && first) setSelected({ repo: first.repo, number: first.number });
  }, [first?.repo, first?.number]);

  const [listWidth, setListWidth] = useWidth("review-list");
  return (
    <div className="review-area">
      <aside className="review-list">
        <Resizer label="Resize the list" edge="right" width={listWidth} min={200} max={480} keep={320} cssVar="--review-list-user" host={area} pane={() => area()?.querySelector<HTMLElement>(":scope > .review-list") ?? null} flexible={() => area()?.querySelector<HTMLElement>(":scope > .pull-view, :scope > .review-empty") ?? null} onChange={setListWidth} />
        <header className="review-list-head" data-tauri-drag-region="deep">
          <h1>Code review</h1>
          <span className="grow" />
          <button
            className="icon-button"
            title="Refresh"
            onClick={async () => {
              setRefreshing(true);
              await Promise.all([crew.loadReviews(true), team && loadTeam(true)]);
              setRefreshing(false);
            }}
          >
            <Icon name="repeat" size={14} className={refreshing ? "spin" : ""} />
          </button>
        </header>
        <div className="review-list-scroll">
          {inbox?.error && <p className="error-text pad">{inbox.error}</p>}
          {!inbox && <p className="faint pad">Asking GitHub…</p>}
          <PullGroup title="Assigned to you" pulls={inbox?.assigned ?? []} selected={selected} onSelect={setSelected} empty="Nothing is assigned to you." />
          <PullGroup title="Asks for your review" pulls={(inbox?.direct ?? []).filter((p) => !inbox?.assigned?.some((a) => a.repo === p.repo && a.number === p.number))} selected={selected} onSelect={setSelected} />
          <PullGroup title="Authored by you" pulls={inbox?.mine ?? []} selected={selected} onSelect={setSelected} />
          <PullGroup
            title="Your teams"
            pulls={team}
            selected={selected}
            onSelect={setSelected}
            collapsed
            onOpen={() => loadTeam()}
            empty={teamError ?? "Nothing asks your teams for a review."}
          />
        </div>
      </aside>
      {selected ? (
        <PullView key={`${selected.repo}#${selected.number}`} crew={crew} selected={selected} onSettings={onSettings} />
      ) : (
        <div className="review-empty faint">Choose a pull request.</div>
      )}
    </div>
  );
}
