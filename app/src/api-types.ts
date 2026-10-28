// Generated from the crewbots API schema by `crewbots openapi --ts`. Do not edit; regenerate.

/** The API this app was built against: its major version, and a fingerprint of all of it. */
export const API_VERSION = 1;
export const API_SCHEMA = "c3947b62c330";

/** One thing a bot did to another: delegated, asked for a review, asked, messaged, posted, or opened or grew a
group chat; or a tool call of a bot's that crewbots refused. */
export interface AgentLogOut {
  id: number;
  at: string;
  kind: "delegate" | "review" | "ask" | "message" | "post" | "open_group" | "add_to_group" | "refused";
  bot: string;
  target: string | null;
  task_id: string | null;
  room_id: string | null;
  text: string;
}

export interface AskBody {
  bot: string;
  question: string;
}

export interface AttachmentCopy {
  path: string;
}

export interface AttachmentData {
  name: string;
  mime: string;
  data: string;
}

export interface AttachmentOut {
  id: string;
  name: string;
  path: string;
  mime: string;
  size: number;
  kind: "image" | "file";
}

export interface AttachmentUpload {
  name: string;
  data: string;
}

export interface Avatar {
  color?: string | null;
  shape?: string | null;
}

/** Whether the crew's background work may run on a plan now, and why (`capacity.py`). */
export interface BackgroundOut {
  plan: string;
  go: boolean;
  reason: string;
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
  context_window: number | null;
  proactive?: boolean;
}

export interface BotUse {
  bot: string;
  tokens: number;
  input: number;
  cached: number;
  written?: number;
}

export interface BringIn {
  projects?: NewProject[];
  memories?: string[];
}

export interface BroughtIn {
  projects: ProjectOut[];
  memories: number;
  task_id: string | null;
}

export interface BrowserInput {
  generation: number;
  lease_generation: number;
  owner: string;
  event: Record<string, unknown>;
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

export interface CacheUse {
  input: number;
  cached: number;
  written?: number;
}

/** A role card's editable parts. Fields left out are left as they are. */
export interface CardBody {
  display_name?: string | null;
  title?: string | null;
  description?: string | null;
  backend?: string | null;
  model?: string | null;
  effort?: string | null;
  context_window?: number | "auto" | null;
  access?: "read_only" | "workspace_write" | "full" | null;
  network?: boolean | string[] | null;
  avatar?: Avatar | null;
  budget?: Budget | null;
  mcp?: string[] | null;
  proactive?: boolean | null;
  instructions?: string | null;
}

export interface CardOut {
  display_name?: string | null;
  title?: string | null;
  description?: string | null;
  backend?: string | null;
  model?: string | null;
  effort?: string | null;
  context_window?: number | "auto" | null;
  access?: "read_only" | "workspace_write" | "full" | null;
  network?: boolean | string[] | null;
  avatar?: Avatar | null;
  budget?: Budget | null;
  mcp?: string[] | null;
  proactive?: boolean | null;
  instructions?: string | null;
  name: string;
}

export interface ChangesOut {
  diff: string | null;
}

export interface ChatMute {
  muted: boolean;
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
  muted?: boolean;
  working: boolean;
  updated_at: string;
  pinned?: number | null;
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

export interface Choice {
  label: string;
  description?: string | null;
}

/** Something in the app's window that failed, for the daemon's log: the app has no terminal of its own. */
export interface ClientError {
  where: string;
  message: string;
  stack?: string | null;
}

export interface Consolidate {
  bot: string;
  weekly?: boolean | null;
}

/** Something that happened around the crew: a commit, a branch, a changed file, a review request, checks that
failed or passed, a thread that finished. */
export interface CrewEventOut {
  id: number;
  at: string;
  kind: string;
  subject: string;
  text: string;
}

export interface CrewState {
  paused: boolean;
  restarting: boolean;
  running: number;
  turns_running: number;
  held: number;
  sessions: number;
  limits: PlanLimitOut[];
  background?: BackgroundOut[];
}

export interface CursorOut {
  id: number;
}

export interface Delegation {
  bot: string;
  goal: string;
  project?: string | null;
  workspace?: string | null;
  review_of?: string | null;
}

export interface DigestOut {
  bot: string;
  items: number;
}

export interface DraftBody {
  path: string;
  line: number;
  side?: "RIGHT" | "LEFT";
  body: string;
}

export interface DraftEdit {
  body: string;
}

/** A line comment for a review, private until the user submits it. */
export interface DraftOut {
  id: number;
  path: string;
  line: number;
  side: "RIGHT" | "LEFT";
  body: string;
  author: string;
  task_id: string | null;
  commit_sha: string | null;
  created_at: string;
  updated_at: string;
}

export interface EffortUse {
  effort: string;
  turns: number;
}

export interface Escalation {
  question: string;
  choices?: Choice[];
  multiple?: boolean;
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

export interface FoundFolderOut {
  folder: string;
  name: string;
  about: string;
  sources: string[];
  sessions: number;
  last_used: string;
  memories: FoundMemoryOut[];
  project: string | null;
}

export interface FoundMemoryOut {
  path: string;
  source: string;
  title: string;
  brought_at?: string | null;
  changed?: boolean;
}

export interface FoundOut {
  folders: FoundFolderOut[];
  memories: FoundMemoryOut[];
}

export interface GroupMember {
  bot: string;
  room?: string | null;
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

export interface ImageOut {
  mime: string;
  data: string;
}

export interface ImportBody {
  id: string;
  name?: string | null;
}

/** Pull requests waiting on you, which never include your own, and your own, apart. Only the lists asked for are
there. */
export interface Inbox {
  assigned?: PullSummary[] | null;
  direct?: PullSummary[] | null;
  team?: PullSummary[] | null;
  mine?: PullSummary[] | null;
  error: string | null;
}

/** A server one of your other apps has, that the crew could copy. */
export interface InstalledServer {
  id: string;
  source: string;
  name: string;
  transport: "stdio" | "http" | "sse";
  command: string | null;
  args: string[];
  url: string | null;
  env_keys: string[];
  header_keys: string[];
  in_crew: boolean;
}

/** A standing intent: when an event of `kind` mentioning `match` happens, its bot is told `text`. */
export interface IntentOut {
  id: number;
  bot: string;
  task_id: string | null;
  kind: string;
  match: string;
  text: string;
  created_at: string;
  expires_at: string;
  fires: number;
  last_fired_at: string | null;
  cancelled: boolean;
}

export interface LastMessage {
  author: string;
  text: string;
  at: string;
}

export interface LeaseRef {
  generation: number;
  lease_generation: number;
  owner: string;
}

export interface LiveBots {
  type: "bots";
}

export interface LiveChatMute {
  type: "chat_mute";
  bot: string;
  muted: boolean;
}

export interface LiveCrew {
  type: "crew";
  crew: CrewState;
}

export interface LiveEvent {
  type: "event";
  event: EventOut;
}

export interface LivePage {
  type: "page";
  bot: string;
  page: string;
}

export interface LivePins {
  type: "pins";
  pins: string[];
}

export interface LiveRoom {
  type: "room";
  room: RoomOut;
}

export interface LiveRoomGone {
  type: "room_gone";
  room_id: string;
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

export interface McpCheck {
  ok: boolean;
  tools: string[];
  error: string | null;
}

export interface McpServerBody {
  transport?: "stdio" | "http" | "sse";
  command?: string | null;
  args?: string[];
  env?: Record<string, string> | null;
  url?: string | null;
  headers?: Record<string, string> | null;
}

/** One of the crew's MCP servers, without the values of its env or headers, which can be keys. */
export interface McpServerOut {
  name: string;
  transport: "stdio" | "http" | "sse";
  command: string | null;
  args: string[];
  url: string | null;
  env_keys: string[];
  header_keys: string[];
  bots: string[];
}

export interface MergeProposal {
  task_id: string;
  summary: string;
}

export interface MergeRequest {
  force?: boolean;
}

export interface MergeResultOut {
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
  merge_state: "proposed" | "merging" | "merged" | null;
  merged_commit: string | null;
  merged_tip: string | null;
  kind: "chat" | "seat" | "thread" | "review" | "ask";
  room_id: string | null;
  pr: string | null;
  routine: string | null;
  budget: number | null;
  created_at: string;
  updated_at: string;
  attempt?: RetryOut | null;
  leftover_files?: string[] | null;
  leftover_check_error?: string | null;
}

export interface MergeSettings {
  auto_merge: boolean;
}

export interface MergeStatusOut {
  already_on: string | null;
}

export interface MergeWithdrawal {
  task_id: string;
  reason: string;
}

export interface Message {
  text?: string;
  attachments?: string[];
  question_id?: number | null;
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
  input: number;
  cached: number;
  written?: number;
}

export interface ModelsOut {
  claude: ModelOut[];
  codex: ModelOut[];
}

export interface NameBody {
  name: string;
}

export interface NewBot {
  display_name?: string | null;
  title?: string | null;
  description?: string | null;
  backend?: string | null;
  model?: string | null;
  effort?: string | null;
  context_window?: number | "auto" | null;
  access?: "read_only" | "workspace_write" | "full" | null;
  network?: boolean | string[] | null;
  avatar?: Avatar | null;
  budget?: Budget | null;
  mcp?: string[] | null;
  proactive?: boolean | null;
  instructions?: string | null;
  name: string;
}

export interface NewNote {
  text: string;
}

export interface NewProject {
  name: string;
  folder: string;
  about?: string;
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

export interface NewThread {
  goal: string;
}

export interface NextCheck {
  minutes: number;
  why: string;
}

export interface NoteOut {
  title: string;
  path: string;
}

export interface OpenGroup {
  title: string;
  members: string[];
  lead?: string | null;
}

export interface OutputOut {
  path: string;
  task_id: string;
  title: string;
  at: string;
}

export interface PageBody {
  text: string;
}

export interface PageEdit {
  page: string;
  find: string;
  replace: string;
}

export interface PageOut {
  name: string;
  path: string;
  updated: string;
  text?: string | null;
}

export interface PageWrite {
  page: string;
  text: string;
}

export interface PermissionAnswer {
  allowed: boolean;
}

export interface PermissionAsk {
  what: string;
  why?: string;
  kind?: "action" | "folder" | "network";
  folder?: string | null;
}

export interface PermissionOut {
  id: number;
  task_id: string;
  kind: string;
  folder: string;
  what: string;
  why: string;
  asked_at: string;
  allowed: boolean | null;
  answered_at: string | null;
}

export interface Pins {
  pins: string[];
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
  windows: PlanWindow[];
  notes: string[];
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

/** Something the crew raised on its own, and how it reached the user. */
export interface ProactiveOut {
  id: number;
  at: string;
  bot: string;
  level: "fyi" | "question" | "needs_you";
  text: string;
  source: string;
  expires_at: string;
  delivered_at: string | null;
  digest_at: string | null;
  reaction: "up" | "down" | null;
}

export interface ProfileOut {
  bot: string;
  working: boolean;
  last_active: string | null;
  threads: TaskOut[];
  outputs: OutputOut[];
}

export interface ProjectBody {
  folder: string;
  about?: string;
  isolation?: "branch" | "worktree" | null;
}

export interface ProjectOut {
  name: string;
  folder: string;
  about: string;
  isolation: "branch" | "worktree";
  missing: boolean;
}

export interface ProjectsOut {
  projects: ProjectOut[];
  errors: Record<string, string>;
  started: boolean;
}

export interface ProposedRoutine {
  name: string;
  description: string;
  goal: string;
  schedule?: string | null;
  on?: string | null;
  repo?: string | null;
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
  base_sha?: string;
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
  people?: string[];
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

export interface Reaction {
  reaction: "up" | "down";
}

export interface Recall {
  query: string;
}

export interface RegistryAdd {
  server: string;
  version: string;
  option: string;
  name: string;
  values?: Record<string, string>;
}

export interface RegistryEntry {
  name: string;
  title: string | null;
  description: string;
  version: string;
  website: string | null;
  options: RegistryOption[];
}

export interface RegistryField {
  kind: "env" | "arg" | "header";
  key: string;
  description: string;
  required: boolean;
  secret: boolean;
  default: string | null;
}

export interface RegistryOption {
  id: string;
  label: string;
  fields: RegistryField[];
}

/** How the last update went, as scripts/release left it. */
export interface ReleaseOut {
  state?: string | null;
  step?: string | null;
  commit?: string | null;
  started_at?: number | null;
  at?: number | null;
  log?: string | null;
  bundle?: string | null;
  expected?: number | null;
  kind?: string | null;
}

export interface Remember {
  title: string;
  body: string;
  shared?: boolean;
}

export interface RemindOn {
  event: string;
  match?: string;
  then: string;
}

export interface RepoFolder {
  folder: string | null;
}

export interface RestartBody {
  when_idle?: boolean;
  timeout_s?: number;
}

/** A task waiting to try again after a failed turn: the try it's waiting for, of how many. */
export interface RetryOut {
  attempt: number;
  of: number;
  wait_seconds: number;
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

/** What to change about a group chat; anything left out stays as it is. */
export interface RoomChange {
  title?: string | null;
  lead?: string | null;
  members?: string[] | null;
  max_depth?: number | null;
  muted?: boolean | null;
  archived?: boolean | null;
}

export interface RoomMessageOut {
  id: number;
  room_id: string;
  author: string;
  text: string;
  depth: number;
  at: string;
  attachments?: AttachmentOut[];
}

export interface RoomOut {
  id: string;
  title: string;
  lead: string;
  members: string[];
  created_at: string;
  updated_at: string;
  max_depth?: number | null;
  stopped?: RoomStop | null;
  muted?: boolean;
  archived_at?: string | null;
  crew?: boolean;
}

export interface RoomStop {
  after: number;
  waiting: string[];
  owed?: string[];
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
  proposed_by?: string | null;
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

export interface SessionRef {
  generation: number;
}

export interface ShareBody {
  files: string[];
  text?: string;
  room?: string | null;
}

export interface SkillOut {
  name: string;
  description: string;
  source: string;
  path: string;
  used: boolean;
  same_as: string | null;
}

export interface StarterOut {
  name: string;
  description: string;
  when: string;
}

export interface StateBody {
  state: string;
}

export type Status = "queued" | "running" | "idle" | "needs_input" | "failed" | "cancelled" | "interrupted" | "held";

export interface Streak {
  longest: number;
  current: number;
}

export interface SubmitReview {
  event: "APPROVE" | "REQUEST_CHANGES" | "COMMENT";
  body?: string;
  drafts?: number[] | null;
}

export interface Submitted {
  url: string;
  state: string;
  comments: number;
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
  merge_state: "proposed" | "merging" | "merged" | null;
  merged_commit: string | null;
  merged_tip: string | null;
  kind: "chat" | "seat" | "thread" | "review" | "ask";
  room_id: string | null;
  pr: string | null;
  routine: string | null;
  budget: number | null;
  created_at: string;
  updated_at: string;
  attempt?: RetryOut | null;
}

/** What /status shows for a chat or a thread. */
export interface TaskStatus {
  task_id: string;
  bot: string;
  backend: string | null;
  plan: string | null;
  model: string | null;
  effort: string | null;
  session_id: string | null;
  open: boolean;
  context_percent: number | null;
  context_window: number | null;
  rollover_percent: number | null;
  turns: number;
  background?: string | null;
}

export interface ThreadBody {
  title: string;
  goal: string;
  project?: string | null;
}

export interface TimezoneIn {
  timezone: string | null;
}

export interface TimezoneOut {
  timezone: string;
  system: string;
  custom: boolean;
}

export interface UpdateBody {
  app?: string | null;
}

export interface UpdateCommit {
  sha: string;
  at: string;
  subject: string;
}

/** Which parts an update needs: the app built again (its code changed since it was built), the daemon restarted
(its code changed since it started). */
export interface UpdateNeeds {
  app?: boolean;
  daemon?: boolean;
}

/** What updating crewbots from its checkout would bring, and how the last update went. */
export interface UpdateOut {
  app: string | null;
  daemon: string | null;
  head: string | null;
  available: boolean;
  commits: UpdateCommit[];
  pull: boolean;
  note: string | null;
  needs?: UpdateNeeds;
  release: ReleaseOut;
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
  cache: CacheUse;
}

export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export type Verdict = "pass" | "fail";

/** What the daemon is, so the app can tell when one of them is out of date. */
export interface VersionOut {
  api: number;
  schema_hash: string;
  build: string | null;
  source_changed: boolean;
}

export interface WithdrawalReason {
  reason: string;
}

export interface WithdrawalResultOut {
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
  merge_state: "proposed" | "merging" | "merged" | null;
  merged_commit: string | null;
  merged_tip: string | null;
  kind: "chat" | "seat" | "thread" | "review" | "ask";
  room_id: string | null;
  pr: string | null;
  routine: string | null;
  budget: number | null;
  created_at: string;
  updated_at: string;
  attempt?: RetryOut | null;
  notice?: string | null;
}
