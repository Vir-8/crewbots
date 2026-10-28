"""The user's push and replace-main buttons. Bot tokens cannot call this route."""

import asyncio

from fastapi import APIRouter

from crewbots.api.context import Context
from crewbots.push import perform
from crewbots.replace_main import perform as replace_main_perform


def routes(c: Context) -> APIRouter:
    v1 = APIRouter()
    lock = asyncio.Lock()

    @v1.post("/push-proposals/{proposal_id}/push")
    async def push(proposal_id: int):
        async with lock:
            return await perform(c.runner, proposal_id)

    @v1.post("/replace-main-proposals/{proposal_id}/replace")
    async def replace_main(proposal_id: int):
        async with lock:
            return replace_main_perform(c.runner, proposal_id)

    return v1
