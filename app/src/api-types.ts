// Generated from the crewbots API schema by `crewbots openapi --ts`. Do not edit; regenerate.

export interface Avatar {
  color?: string | null;
  shape?: string | null;
}

export interface BotOut {
  name: string;
  title: string;
  description: string;
  backend: string;
  model: string | null;
  access: string;
  workspace: string | null;
  network: boolean | string[];
  mcp: string[];
  skills: string[];
  can_delegate: boolean;
  path: string | null;
  display_name: string | null;
  effort: string | null;
  avatar: Record<string, string> | null;
}

export interface BotUse {
  bot: string;
  tokens: number;
}

export interface Budget {
  tokens: number;
  per?: "day" | "week";
}

export interface BudgetOut {
  bot: string;
  tokens: number | null;
  per: "day" | "week";
  used: number;
  held: string[];
}

/** A role card's editable parts. Fields left out are left as they are. */
export interface CardBody {
  display_name?: string | null;
  title?: string | null;
  description?: string | null;
  backend?: string | null;
  model?: string | null;
  effort?: string | null;
  access?: "read_only" | "workspace_write" | "full" | null;
  network?: boolean | string[] | null;
  avatar?: Avatar | null;
  budget?: Budget | null;
  instructions?: string | null;
}

export interface CardOut {
  display_name?: string | null;
  title?: string | null;
  description?: string | null;
  backend?: string | null;
  model?: string | null;
  effort?: string | null;
  access?: "read_only" | "workspace_write" | "full" | null;
  network?: boolean | string[] | null;
  avatar?: Avatar | null;
  budget?: Budget | null;
  instructions?: string | null;
  name: string;
}

export interface ChangesOut {
  diff: string | null;
}

export interface ChatOut {
  key: string;
  kind: "direct" | "group";
  title: string;
  bot: string | null;
  task_id: string | null;
  room: RoomOut | null;
  last: LastMessage | null;
  unread: number;
  working: boolean;
  updated_at: string;
}

export interface Checks {
  total: number;
  passed: number;
  failed: number;
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

export interface EffortUse {
  effort: string;
  turns: number;
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

/** Pull requests waiting on you, which never include your own, and your own, apart. */
export interface Inbox {
  assigned: PullSummary[];
  direct: PullSummary[];
  team: PullSummary[];
  mine: PullSummary[];
  error: string | null;
}

export interface LastMessage {
  author: string;
  text: string;
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

export interface LiveRoom {
  type: "room";
  room: RoomOut;
}

export interface LiveRoomMessage {
  type: "room_message";
  message: RoomMessageOut;
}

export interface LiveTask {
  type: "task";
  task: TaskOut;
}

export interface LongestTurn {
  seconds: number;
  task_id: string | null;
  title: string;
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

export interface ModelOut {
  id: string;
  label: string;
  description: string;
  efforts: string[];
  default_effort: string | null;
  default: boolean;
}

export interface ModelUse {
  plan: string;
  model: string;
  tokens: number;
}

export interface ModelsOut {
  claude: ModelOut[];
  codex: ModelOut[];
}

export interface NewBot {
  display_name?: string | null;
  title?: string | null;
  description?: string | null;
  backend?: string | null;
  model?: string | null;
  effort?: string | null;
  access?: "read_only" | "workspace_write" | "full" | null;
  network?: boolean | string[] | null;
  avatar?: Avatar | null;
  budget?: Budget | null;
  instructions?: string | null;
  name: string;
}

export interface NewRoom {
  title?: string;
  members: string[];
  lead?: string | null;
}

export interface NewRoutine {
  description?: string | null;
  bot?: string | null;
  schedule?: string | null;
  on?: string | null;
  repo?: string | null;
  workspace?: string | null;
  budget?: number | null;
  enabled?: boolean | null;
  goal?: string | null;
  name: string;
}

export interface NewTask {
  bot: string;
  goal: string;
  workspace?: string | null;
}

export interface OutputOut {
  path: string;
  task_id: string;
  title: string;
  at: string;
}

export interface PlanLimitOut {
  plan: string;
  window: string;
  status: "ok" | "warning" | "limited";
  used_percent: number | null;
  resets_at: number | null;
}

export interface PlanOut {
  available: boolean;
  error: string | null;
  plan: string | null;
  windows: PlanWindow[];
  checked_at: number;
}

export interface PlanWindow {
  name: string;
  used_percent: number;
  resets_at: number | null;
}

export interface PlansOut {
  claude: PlanOut;
  codex: PlanOut;
}

export interface PostBody {
  text: string;
  room?: string | null;
}

export interface ProfileOut {
  bot: string;
  working: boolean;
  last_active: string | null;
  threads: TaskOut[];
  outputs: OutputOut[];
}

export interface PullFile {
  path: string;
  additions: number;
  deletions: number;
}

export interface PullOut {
  repo: string;
  number: number;
  title: string;
  body: string;
  url: string;
  state: string | null;
  draft: boolean;
  author: string;
  head: string;
  base: string;
  head_sha: string;
  additions: number;
  deletions: number;
  changed_files: number;
  review_decision: string | null;
  created_at: string | null;
  updated_at: string | null;
  labels: string[];
  files: PullFile[];
  checks: Checks;
  failing: string[];
  issues: number[];
  checkout: string | null;
  reviews: TaskOut[];
}

export interface PullSummary {
  repo: string;
  number: number;
  title: string;
  url: string;
  draft: boolean;
  author: string;
  author_avatar: string | null;
  created_at: string | null;
  updated_at: string | null;
  additions: number;
  deletions: number;
  changed_files: number;
  review_decision: string | null;
  checks: string | null;
  reviewed_at?: string | null;
}

export interface Recall {
  query: string;
}

export interface Remember {
  title: string;
  body: string;
  shared?: boolean;
}

export interface RepoFolder {
  folder: string | null;
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

export interface ReviewRequest {
  note?: string;
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

export interface Reviewer {
  backend: "claude" | "codex";
  model?: string | null;
  effort?: string | null;
}

export interface RoomMessageOut {
  id: number;
  room_id: string;
  author: string;
  text: string;
  depth: number;
  at: string;
}

export interface RoomOut {
  id: string;
  title: string;
  lead: string;
  members: string[];
  created_at: string;
  updated_at: string;
}

export interface RoutineBody {
  description?: string | null;
  bot?: string | null;
  schedule?: string | null;
  on?: string | null;
  repo?: string | null;
  workspace?: string | null;
  budget?: number | null;
  enabled?: boolean | null;
  goal?: string | null;
}

export interface RoutineFileOut {
  description?: string | null;
  bot?: string | null;
  schedule?: string | null;
  on?: string | null;
  repo?: string | null;
  workspace?: string | null;
  budget?: number | null;
  enabled?: boolean | null;
  goal?: string | null;
  name: string;
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
  crew_error: string | null;
  starters: StarterOut[];
}

export interface SearchHit {
  task_id: string;
  kind: string;
  at: string;
  snippet: string;
  bot: string;
  title: string;
}

export interface StarterOut {
  name: string;
  description: string;
  when: string;
}

export type Status = "queued" | "running" | "idle" | "needs_input" | "failed" | "cancelled" | "interrupted" | "held";

export interface Streak {
  longest: number;
  current: number;
}

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
  kind: "chat" | "seat" | "thread" | "review";
  room_id: string | null;
  pr: string | null;
  routine: string | null;
  budget: number | null;
  created_at: string;
  updated_at: string;
}

export interface ThreadBody {
  title: string;
  goal: string;
}

export interface UsageCounts {
  threads: number;
  reviews: number;
  routine_runs: number;
  room_messages: number;
}

export interface UsageDay {
  date: string;
  claude: number;
  codex: number;
}

export interface UsagePeak {
  date: string;
  tokens: number;
}

/** The crew's own use, in fresh tokens. */
export interface UsageSummary {
  days: number;
  daily: UsageDay[];
  totals: Record<string, number>;
  turns: number;
  peak: UsagePeak | null;
  longest_turn: LongestTurn | null;
  streak: Streak;
  models: ModelUse[];
  bots: BotUse[];
  efforts: EffortUse[];
  counts: UsageCounts;
}

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export type Verdict = "pass" | "fail";
