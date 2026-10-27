// The demo crew's answers for the pages and lists the bridge serves: chats, code review's inbox and drafts, usage and
// plans, tools, routines and the crew's state.

import type { ConnectionStatus } from "../types";
import * as demo from "./demo";

export const status: ConnectionStatus = { state: "connected", home: "design preview", ownsDaemon: false };

/** The chats pinned to the top, in order. */
export let pins = ["bot:chief", "room:r_crew000001"];
export const setPins = (keys: string[]) => (pins = keys);

export function chats() {
  const direct = demo.bots.map((b) => {
    const id = demo.chatIds[b.name];
    const events = demo.events.get(id) ?? [];
    const last = [...events].reverse().find((e) => e.kind === "text" || (e.kind === "message" && e.data.from === "user"));
    const family = [...demo.tasks.values()].filter((t) => t.parent_id === id || t.id === id);
    return {
      key: `bot:${b.name}`, kind: "direct", title: b.title, bot: b.name, task_id: id, room: null,
      last: last ? { author: last.kind === "text" ? b.name : "user", text: String(last.data.text), at: last.at } : null,
      unread: b.name === "engineer" ? 1 : 0, working: family.some((t) => t.status === "running") || b.name === "engineer",
      updated_at: last?.at ?? "",
    };
  });
  const groups = demo.rooms.map((room) => {
    const last = demo.roomMessages.get(room.id)?.at(-1);
    return { key: `room:${room.id}`, kind: "group", title: room.title, bot: null, task_id: null, room, last: last ? { author: last.author, text: last.text, at: last.at } : null, unread: 0, working: false, updated_at: last?.at ?? room.updated_at };
  });
  return [...direct, ...groups].map((c) => ({ ...c, pinned: pins.includes(c.key) ? pins.indexOf(c.key) : null }));
}

export const reviewInbox = {
  direct: [
    { repo: "acme/web", number: 412, title: "Move the export button into the reports toolbar", url: "https://github.com/acme/web/pull/412", draft: false, author: "sam", author_avatar: null, created_at: new Date(Date.now() - 5 * 3600e3).toISOString(), updated_at: new Date(Date.now() - 3600e3).toISOString(), additions: 40, deletions: 12, changed_files: 3, review_decision: "REVIEW_REQUIRED", checks: "SUCCESS", reviewed_at: null },
    { repo: "acme/web", number: 409, title: "Fix the date filter's off-by-one at month boundaries", url: "https://github.com/acme/web/pull/409", draft: false, author: "lee", author_avatar: null, created_at: new Date(Date.now() - 30 * 3600e3).toISOString(), updated_at: new Date(Date.now() - 20 * 3600e3).toISOString(), additions: 18, deletions: 6, changed_files: 2, review_decision: "REVIEW_REQUIRED", checks: "FAILURE", reviewed_at: new Date().toISOString() },
  ],
  assigned: [
    { repo: "acme/web", number: 401, title: "Upgrade the charts library", url: "https://github.com/acme/web/pull/401", draft: false, author: "vir", author_avatar: null, created_at: new Date(Date.now() - 70 * 3600e3).toISOString(), updated_at: new Date(Date.now() - 60 * 3600e3).toISOString(), additions: 210, deletions: 180, changed_files: 9, review_decision: null, checks: "SUCCESS", reviewed_at: null },
  ],
  team: [
    { repo: "acme/api", number: 88, title: "Paginate the audit log endpoint", url: "https://github.com/acme/api/pull/88", draft: true, author: "kim", author_avatar: null, created_at: new Date(Date.now() - 50 * 3600e3).toISOString(), updated_at: new Date(Date.now() - 48 * 3600e3).toISOString(), additions: 120, deletions: 30, changed_files: 6, review_decision: null, checks: "PENDING", reviewed_at: null },
  ],
  mine: [],
  error: null,
};

export function usageSummary(days: number) {
  // Quiet half a year ago, busier lately, with weekends mostly off; each day the same whatever the range.
  const daily = Array.from({ length: days }, (_, i) => {
    const date = new Date(Date.now() - (days - 1 - i) * 86400e3);
    const n = Math.floor(date.getTime() / 86400e3);
    const ramp = Math.max(0, 1 - (days - 1 - i) / 180);
    const weekend = date.getDay() === 0 || date.getDay() === 6;
    const on = ramp > 0 && Math.abs(Math.sin(n * 12.9898) * 43758.5453) % 1 < (weekend ? 0.2 : 0.85) * ramp + 0.1;
    const size = on ? Math.round(ramp * (180_000 + 420_000 * (Math.abs(Math.sin(n * 78.233) * 9973) % 1))) : 0;
    return { date: date.toISOString().slice(0, 10), claude: Math.round(size * 0.45), codex: Math.round(size * 0.55) };
  });
  const totals = { claude: daily.reduce((a, d) => a + d.claude, 0), codex: daily.reduce((a, d) => a + d.codex, 0) };
  const peak = daily.reduce((a, d) => (d.claude + d.codex > a.claude + a.codex ? d : a));
  return {
    days, daily, totals, turns: 1_412,
    peak: { date: peak.date, tokens: peak.claude + peak.codex },
    longest_turn: { seconds: 2_710, task_id: "t_csv0000002", title: "Add CSV export to the reports page" },
    streak: { longest: 12, current: 4 },
    models: [
      { plan: "codex", model: "gpt-6.1-sol", tokens: Math.round(totals.codex * 0.8), input: Math.round(totals.codex * 3.1), cached: Math.round(totals.codex * 2.4) },
      { plan: "claude", model: "opus", tokens: Math.round(totals.claude * 0.55), input: Math.round(totals.claude * 2.6), cached: Math.round(totals.claude * 2.2) },
      { plan: "claude", model: "sonnet", tokens: Math.round(totals.claude * 0.45), input: Math.round(totals.claude * 1.9), cached: Math.round(totals.claude * 1.5) },
      { plan: "codex", model: "gpt-6-luna", tokens: Math.round(totals.codex * 0.2), input: Math.round(totals.codex * 0.7), cached: Math.round(totals.codex * 0.4) },
    ],
    bots: [
      { bot: "engineer", tokens: Math.round(totals.codex * 0.7), input: Math.round(totals.codex * 0.7) * 4, cached: Math.round(totals.codex * 0.7) * 3 }, { bot: "chief", tokens: Math.round(totals.claude * 0.5), input: Math.round(totals.claude * 0.5) * 4, cached: Math.round(totals.claude * 0.5) * 3 },
      { bot: "reviewer", tokens: Math.round(totals.claude * 0.3), input: Math.round(totals.claude * 0.3) * 4, cached: Math.round(totals.claude * 0.3) * 3 }, { bot: "PR reviewer", tokens: Math.round((totals.claude + totals.codex) * 0.12) },
      { bot: "critic", tokens: Math.round(totals.codex * 0.18), input: Math.round(totals.codex * 0.18) * 4, cached: Math.round(totals.codex * 0.18) * 3 },
    ],
    efforts: [{ effort: "high", turns: 812 }, { effort: "medium", turns: 433 }, { effort: "xhigh", turns: 167 }],
    counts: { threads: 64, reviews: 23, routine_runs: 41, room_messages: 318 },
    cache: { input: (totals.claude + totals.codex) * 4, cached: (totals.claude + totals.codex) * 3, written: Math.round((totals.claude + totals.codex) * 0.4) },
  };
}

export const plans = {
  claude: {
    available: true, error: null, notes: [], checked_at: Date.now() / 1000,
    windows: [
      { name: "Session", used_percent: 37, resets_at: demo.resetsAt },
      { name: "Week · all models", used_percent: 12, resets_at: demo.resetsAt + 3 * 86400 },
      { name: "Week · Fable", used_percent: 4, resets_at: demo.resetsAt + 3 * 86400 },
    ],
  },
  codex: {
    available: true, error: null, notes: ["12,400 credits"], checked_at: Date.now() / 1000,
    windows: [
      { name: "5 hours", used_percent: 84, resets_at: demo.resetsAt - 3600 },
      { name: "Week", used_percent: 11, resets_at: demo.resetsAt + 5 * 86400 },
    ],
  },
};

export const previewFiles = new Map<string, { name: string; mime: string; data: string }>();

export const reviewDrafts: { id: number; path: string; line: number; side: string; body: string; author: string; task_id: string | null; commit_sha: string; created_at: string; updated_at: string }[] = [
  { id: 1, path: "server/reports.py", line: 64, side: "RIGHT", body: "`filename` is fixed, so two exports in a row overwrite each other in the browser's downloads. Put the date in it.", author: "reviewer:claude:sonnet:high", task_id: null, commit_sha: "abc", created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
];

export const mcpServers = [
  { name: "blender", transport: "stdio", command: "uv", args: ["--directory", "~/blender_mcp/mcp", "run", "blender-mcp"], url: null, env_keys: ["BLENDER_MCP_PORT"], header_keys: [], bots: ["engineer"] },
  { name: "davinci-resolve", transport: "stdio", command: "/bin/sh", args: ["start-resolve-mcp.sh"], url: null, env_keys: ["RESOLVE_SCRIPT_API"], header_keys: [], bots: [] },
];

export const skills = [
  { name: "arena", description: "Two or more agents attempt the same task independently; pick the best.", source: "crew", path: "~/.crewbots/crew/skills/arena/SKILL.md", used: true, same_as: null },
  { name: "interrogate", description: "Question the user until the goal is unambiguous.", source: "crew", path: "~/.crewbots/crew/skills/interrogate/SKILL.md", used: true, same_as: null },
  { name: "pdf", description: "Read, fill and make PDF files.", source: "~/.claude/skills", path: "~/.claude/skills/synced/x/pdf/SKILL.md", used: true, same_as: null },
  { name: "unity-mcp-orchestrator", description: "Orchestrate the Unity Editor through MCP for Unity.", source: "~/.claude/skills", path: "~/.claude/skills/unity-mcp-skill/SKILL.md", used: true, same_as: null },
  { name: "graphify", description: "Turn a codebase into a knowledge graph.", source: "~/.claude/skills", path: "~/.claude/skills/graphify/SKILL.md", used: true, same_as: null },
  { name: "graphify", description: "Turn a codebase into a knowledge graph.", source: "~/.codex/skills", path: "~/.codex/skills/graphify/SKILL.md", used: false, same_as: "~/.claude/skills" },
];

export const crewState = {
  paused: false, restarting: false, running: 1, turns_running: 1, held: 1, sessions: 2,
  limits: [{ plan: "claude", window: "five_hour", status: "limited", used_percent: 100, resets_at: demo.resetsAt }],
};

export function routines() {
  const at = (days: number, hour: number) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    d.setHours(hour, 0, 0, 0);
    return d.toISOString();
  };
  const base = { workspace: null, repo: null, on: null, budget: null, enabled: true, last_run_at: null, last_task_id: null, skipped_at: null };
  return {
    routines: [
      { ...base, name: "standup", description: "A short summary each weekday morning of what the crew did, and what needs you.", bot: "chief", when: "0 9 * * 1-5", schedule: "0 9 * * 1-5", path: "routines/standup.md", next_run_at: at(1, 9), last_run_at: at(0, 9), last_task_id: "t_stand00001", budget: 200_000 },
      { ...base, name: "retro", description: "A Friday look back at the week.", bot: "chief", when: "0 16 * * 5", schedule: "0 16 * * 5", path: "routines/retro.md", next_run_at: at(4, 16) },
      { ...base, name: "dependency-check", description: "Looks for outdated or vulnerable dependencies every Monday.", bot: "engineer", when: "0 10 * * 1", schedule: "0 10 * * 1", path: "routines/dependency-check.md", next_run_at: null, enabled: false, proposed_by: "engineer" },
      { ...base, name: "review-requests", description: "Drafts a review when someone asks you to review a pull request.", bot: "reviewer", when: "on github.review_requested in acme/web", schedule: null, on: "github.review_requested", repo: "acme/web", path: "routines/review-requests.md", next_run_at: null },
    ],
    errors: {},
    github_error: null,
    crew_error: null,
    starters: [{ name: "ci-failed", description: "Works out why checks failed on one of your pull requests, and what would fix it.", when: "on github.ci_failed in owner/name" }],
  };
}
