"""The crew's use: a year of tokens, the plans' limits, and each bot's budget."""

import asyncio

from fastapi import APIRouter, Query

from crewbots import plans as plans_module
from crewbots import usage as usage_module
from crewbots.api.context import Context
from crewbots.api.schemas import BudgetOut, PlansOut, UsageSummary


def routes(c: Context) -> APIRouter:
    runner, store = c.runner, c.store
    v1 = APIRouter()

    @v1.get("/usage/summary", response_model=UsageSummary)
    async def usage_summary(days: int = Query(371, ge=7, le=731)):
        """The crew's use over the last `days`: a year and a week by default, for the activity grid."""
        return await asyncio.to_thread(usage_module.summary, store, runner.bot_for, days)

    @v1.get("/plans", response_model=PlansOut)
    async def get_plans(fresh: bool = False):
        """How much of each plan's limits is used, and when they reset, asked of the CLIs. Kept answers come back at
        once (`checked_at` says how old they are); `fresh` waits for new ones."""
        return await plans_module.plans(fresh)

    @v1.get("/budgets", response_model=list[BudgetOut])
    async def budgets():
        return runner.budgets()

    return v1
