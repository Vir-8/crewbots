"""The user's push button. Bot tokens cannot call this route."""

import asyncio

from fastapi import APIRouter

from crewbots.api.context import Context
from crewbots.push import perform


def routes(c: Context) -> APIRouter:
    v1 = APIRouter()
    lock = asyncio.Lock()

    @v1.post("/push-proposals/{proposal_id}/push")
    async def push(proposal_id: int):
        async with lock:
            return await perform(c.runner, proposal_id)

    return v1
