// Generated from the crewbots API schema by `crewbots openapi --ts`. Do not edit; regenerate.

export interface BotOut {
  name: string;
  title: string;
  description: string;
  backend: string;
  model: string | null;
  access: string;
  workspace: string | null;
}

export interface BudgetOut {
  bot: string;
  tokens: number | null;
  per: "day" | "week";
  used: number;
  held: string[];
}

export interface ChangesOut {
  diff: string | null;
}

export interface ChildMessage {
  task_id: string;
  text: string;
}

export interface ChildOut {
  id: string;
  bot: string;
  title: string;
  status: Status;
  worktree?: string | null;
  branch?: string | null;
  handoff?: HandoffOut | null;
  result?: string | null;
  error?: string | null;
}

export interface CrewState {
  paused: boolean;
  running: number;
  held: number;
  sessions: number;
  limits: PlanLimitOut[];
}

export interface CursorOut {
  id: number;
}

export interface Delegation {
  bot: string;
  goal: string;
  workspace?: string | null;
  review_of?: string | null;
}

export interface Escalation {
  question: string;
}

export interface EventOut {
  id: number;
  task_id: string;
  kind: string;
  data: Record<string, unknown>;
  at: string;
}

export interface Finding {
  location: string;
  problem: string;
  scenario?: string;
}

export interface FindingOut {
  location?: string | null;
  problem?: string | null;
  scenario?: string | null;
}

export interface Forget {
  name: string;
  shared?: boolean;
}

export interface HTTPValidationError {
  detail?: ValidationError[];
}

export interface HandoffBody {
  done: string;
  output?: string[];
  open?: string;
  needs?: string;
}

export interface HandoffOut {
  id: number;
  task_id: string;
  from_bot: string;
  done: string;
  output: string[];
  open: string;
  needs: string;
  at: string;
}

export interface LiveCrew {
  type: "crew";
  crew: CrewState;
}

export interface LiveEvent {
  type: "event";
  event: EventOut;
}

export interface LiveTask {
  type: "task";
  task: TaskOut;
}

export interface MergeProposal {
  task_id: string;
  summary: string;
}

export interface MergeRequest {
  force?: boolean;
}

export interface Message {
  text: string;
}

export interface NewTask {
  bot: string;
  goal: string;
  workspace?: string | null;
}

export interface PlanLimitOut {
  plan: string;
  window: string;
  status: "ok" | "warning" | "limited";
  used_percent: number | null;
  resets_at: number | null;
}

export interface Recall {
  query: string;
}

export interface Remember {
  title: string;
  body: string;
  shared?: boolean;
}

export interface ReviewBody {
  verdict: Verdict;
  summary: string;
  findings?: Finding[];
}

export interface ReviewEntryOut {
  task_id: string;
  reviewer: string;
  status: Status;
  verdict: Verdict | null;
  summary: string | null;
  findings: FindingOut[];
}

export interface ReviewStateOut {
  work_id: string;
  version: number;
  round: number;
  max_rounds: number;
  approved: boolean;
  failed: boolean;
  out_of_rounds: boolean;
  reviews: ReviewEntryOut[];
}

export interface RoutineOut {
  name: string;
  description: string;
  bot: string;
  when: string;
  schedule: string | null;
  on: string | null;
  repo: string | null;
  workspace: string | null;
  budget: number | null;
  enabled: boolean;
  path: string;
  last_run_at: string | null;
  last_task_id: string | null;
  next_run_at: string | null;
  skipped_at: string | null;
}

export interface RoutinesOut {
  routines: RoutineOut[];
  errors: Record<string, string>;
  github_error: string | null;
}

export interface SearchHit {
  task_id: string;
  kind: string;
  at: string;
  snippet: string;
  bot: string;
  title: string;
}

export type Status = "queued" | "running" | "idle" | "needs_input" | "failed" | "cancelled" | "interrupted" | "held";

export interface TaskOut {
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
  routine: string | null;
  budget: number | null;
  created_at: string;
  updated_at: string;
}

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export type Verdict = "pass" | "fail";
