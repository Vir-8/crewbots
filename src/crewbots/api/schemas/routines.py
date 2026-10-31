"""Routines: work the crew starts on a schedule or a trigger."""


from pydantic import BaseModel


class RoutineBody(BaseModel):
    description: str | None = None
    bot: str | None = None
    schedule: str | None = None
    on: str | None = None
    repo: str | None = None
    workspace: str | None = None
    budget: int | None = None
    enabled: bool | None = None
    goal: str | None = None

class NewRoutine(RoutineBody):
    name: str

class RoutineFileOut(RoutineBody):
    name: str

class RoutineOut(BaseModel):
    name: str
    description: str
    bot: str
    when: str  # the cron schedule, or the trigger
    schedule: str | None
    on: str | None
    repo: str | None
    workspace: str | None
    budget: int | None
    enabled: bool
    path: str
    last_run_at: str | None
    last_task_id: str | None
    next_run_at: str | None
    skipped_at: str | None
    proposed_by: str | None = None  # a bot's proposal, off until the user turns it on

class StarterOut(BaseModel):
    name: str
    description: str
    when: str

class RoutinesOut(BaseModel):
    routines: list[RoutineOut]
    errors: dict[str, str]  # routine file, or a checklist's path in the crew folder → what's wrong with it
    github_error: str | None
    crew_error: str | None  # a role card or team file that couldn't be read; the crew carries on as it was
    starters: list[StarterOut]  # starter routines this crew doesn't have yet
