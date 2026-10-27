// The daemon's shapes come from its schema (api-types.ts, generated). This file only names them for the UI.

import type {
  BotOut,
  BudgetOut,
  CardOut,
  ChatOut,
  Inbox as InboxOut,
  ModelOut,
  ModelsOut,
  Reviewer as ReviewerOut,
  PullOut,
  PullSummary as PullSummaryOut,
  RoutineFileOut,
  UsageSummary as UsageSummaryOut,
  PlansOut,
  TaskStatus as TaskStatusOut,
  McpServerOut,
  InstalledServer,
  RegistryEntry,
  McpCheck,
  SkillOut,
  DraftOut,
  AttachmentOut,
  Submitted as SubmittedOut,
  CrewState as CrewStateOut,
  EventOut,
  FindingOut,
  LiveCrew,
  LiveEvent,
  LiveRoom,
  LiveRoomGone,
  LiveRoomMessage,
  LivePins,
  LiveTask,
  ProfileOut,
  ReviewStateOut,
  RoomMessageOut,
  RoomChange,
  RoomOut,
  RoutineOut,
  RoutinesOut,
  Status as StatusOut,
  TaskOut,
} from "./api-types";

export type Status = StatusOut;
export type Task = TaskOut;
export type StoredEvent = Omit<EventOut, "data"> & { data: Record<string, any> }; // each kind has its own fields
export type Bot = BotOut;
export type Finding = FindingOut;
export type ReviewState = ReviewStateOut;
export type Live = LiveTask | LiveCrew | LiveRoom | LiveRoomGone | LiveRoomMessage | LivePins | (Omit<LiveEvent, "event"> & { event: StoredEvent });
export type Chat = ChatOut;
export type Room = RoomOut;
export type { RoomChange };
export type RoomMessage = RoomMessageOut;
export type Profile = ProfileOut;
export type Card = CardOut;
export type RoutineFile = RoutineFileOut;
export type UsageSummary = UsageSummaryOut;
export type Plans = PlansOut;
export type TaskStatus = TaskStatusOut;
export type McpServerInfo = McpServerOut;
export type InstalledMcp = InstalledServer;
export type RegistryServer = RegistryEntry;
export type McpCheckResult = McpCheck;
export type Skill = SkillOut;
export type Draft = DraftOut;
export type Attachment = AttachmentOut;
export type Submitted = SubmittedOut;
export type ReviewInbox = InboxOut;
export type PullSummary = PullSummaryOut;
export type Pull = PullOut;
export type Model = ModelOut;
export type Models = ModelsOut;
export type Reviewer = ReviewerOut;
export type CrewState = CrewStateOut;
export type Routine = RoutineOut;
export type Routines = RoutinesOut;
export type Budget = BudgetOut;

export interface ConnectionStatus {
  state: "connecting" | "starting" | "connected" | "reconnecting" | "no-crew" | "error";
  home: string;
  url?: string;
  message?: string;
  ownsDaemon?: boolean;
}

export const ACTIVE: Status[] = ["queued", "running"];
