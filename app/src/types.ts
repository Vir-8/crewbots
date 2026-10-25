// The daemon's shapes, as its API returns them (see src/crewbots/store.py and runner.py).

export type Status = "queued" | "running" | "idle" | "needs_input" | "failed" | "cancelled" | "interrupted";

export interface Task {
  id: string;
  bot: string;
  title: string;
  status: Status;
  created_by: string;
  parent_id: string | null;
  workspace: string | null;
  repo: string | null;
  worktree_path: string | null;
  branch: string | null;
  base: string | null;
  session_id: string | null;
  result: string | null;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  reported: boolean;
  turns: number;
  review_of: string | null;
  review_version: number | null;
  merge_state: "proposed" | "merged" | null;
  merged_commit: string | null;
  created_at: string;
  updated_at: string;
}

export interface StoredEvent {
  id: number;
  task_id: string;
  kind: string;
  data: Record<string, any>;
  at: string;
}

export interface Bot {
  name: string;
  title: string;
  description: string;
  backend: string;
  model: string | null;
  access: string;
  workspace: string | null;
}

export interface Finding {
  location?: string;
  problem?: string;
  scenario?: string;
}

export interface ReviewState {
  work_id: string;
  version: number;
  round: number;
  max_rounds: number;
  approved: boolean;
  failed: boolean;
  out_of_rounds: boolean;
  reviews: {
    task_id: string;
    reviewer: string;
    status: Status;
    verdict: "pass" | "fail" | null;
    summary: string | null;
    findings: Finding[];
  }[];
}

export type Live = { type: "task"; task: Task } | { type: "event"; event: StoredEvent };

export interface ConnectionStatus {
  state: "connecting" | "starting" | "connected" | "reconnecting" | "no-crew" | "error";
  home: string;
  url?: string;
  message?: string;
  ownsDaemon?: boolean;
}

export const ACTIVE: Status[] = ["queued", "running"];
