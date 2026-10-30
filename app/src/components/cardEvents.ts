// What the events behind a thread's cards carry, as the daemon records them. The event schema leaves their data
// open, so build() (threadBlocks.ts) gives each card its shape here.

import type { HandoffOut, Verdict } from "../api-types";

/** A handback (store/records.py); one from before evidence and checks were kept has neither. */
export type Handoff = HandoffOut & { evidence?: string[]; checks?: string[] };

/** A reviewer's finding (tool_args.py); one from before proof was kept has none. */
export type Finding = { location: string; problem: string; scenario?: string; proof?: string };

/** A reviewer's verdict (store/records.py). */
export type Review = { reviewer: string; version: number; verdict: Verdict; summary: string; findings: Finding[] };

/** The CI a proposal's commit passed (ci.py). */
export type Ci = { note: string; steps: string[] };

/** A Push card (push.py): `error` when it couldn't be made, and the rest as far as it got. */
export type PushProposal = {
  project: string;
  summary: string;
  branch?: string;
  upstream?: string;
  push_url_display?: string;
  commits?: string[];
  ci?: Ci;
  error?: string;
};

/** A Replace-main card (replace_main.py), the same way. */
export type ReplaceProposal = {
  project: string;
  summary: string;
  branch: string;
  target?: string;
  backup?: string;
  upstream?: string;
  old_commits?: number;
  new_commits?: number;
  checks?: { name: string; ok: boolean; detail: string }[];
  ci?: Ci;
  error?: string;
};

/** What a card's button did. */
export type Outcome = { ok: boolean; reason: string; pushed?: boolean };
