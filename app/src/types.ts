// The daemon's shapes come from its schema (api-types.ts, generated). This file only names them for the UI.

import type {
  BotOut,
  BudgetOut,
  CrewState as CrewStateOut,
  EventOut,
  FindingOut,
  LiveCrew,
  LiveEvent,
  LiveTask,
  ReviewStateOut,
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
export type Live = LiveTask | LiveCrew | (Omit<LiveEvent, "event"> & { event: StoredEvent });
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
