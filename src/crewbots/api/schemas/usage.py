"""The crew's use of its plans: limits, a year of tokens, and each bot's budget."""

from typing import Literal

from pydantic import BaseModel


class PlanWindow(BaseModel):
    name: str  # in the CLI's own terms: "Session", "Week · Fable", "5 hours", "Week"
    used_percent: float
    resets_at: int | None  # unix time

class PlanOut(BaseModel):
    available: bool
    error: str | None
    windows: list[PlanWindow]
    notes: list[str]  # anything else the CLI says about the plan: credits left, free resets, why there are no limits
    checked_at: float  # unix time; older than a couple of minutes means a fresh answer is on its way

class PlansOut(BaseModel):
    claude: PlanOut
    codex: PlanOut

class UsageDay(BaseModel):
    date: str
    claude: int
    codex: int

class UsagePeak(BaseModel):
    date: str
    tokens: int

class LongestTurn(BaseModel):
    seconds: int
    task_id: str | None
    title: str

class Streak(BaseModel):
    longest: int
    current: int

class ModelUse(BaseModel):
    plan: str
    model: str
    tokens: int
    input: int  # all input, cached or not
    cached: int  # input the model had cached
    written: int = 0  # input written to the cache (Claude reports it)

class BotUse(BaseModel):
    bot: str
    tokens: int
    input: int
    cached: int
    written: int = 0

class CacheUse(BaseModel):
    input: int
    cached: int
    written: int = 0

class EffortUse(BaseModel):
    effort: str
    turns: int

class UsageCounts(BaseModel):
    threads: int
    reviews: int
    routine_runs: int
    room_messages: int

class UsageSummary(BaseModel):
    """The crew's own use, in fresh tokens."""

    days: int
    daily: list[UsageDay]
    totals: dict[str, int]
    turns: int
    peak: UsagePeak | None
    longest_turn: LongestTurn | None
    streak: Streak
    models: list[ModelUse]
    bots: list[BotUse]
    efforts: list[EffortUse]
    counts: UsageCounts
    cache: CacheUse

class BudgetOut(BaseModel):
    bot: str
    tokens: int | None  # None: no budget
    per: Literal["day", "week"]
    used: int  # fresh tokens over the last day or week
    held: list[str]
