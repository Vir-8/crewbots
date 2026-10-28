"""Authenticated control of an existing task tab; this API never starts a browser."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from crewbots.api.context import Context
from crewbots.browser_cdp import BrowserError
from crewbots.runner import UnknownBot
from crewbots.store import Status


class SessionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: int


class LeaseRef(SessionRef):
    lease_generation: int
    owner: str


class BrowserInput(LeaseRef):
    event: dict[str, Any]


def routes(c: Context) -> APIRouter:
    v1 = APIRouter()
    browser = c.runner.browser

    def policy(task_id):
        def current():
            try:
                task = c.store.get_task(task_id)
                bot = c.runner.bot_for(task.bot)
            except (KeyError, UnknownBot):
                return False, False
            network = True if any(g["kind"] == "network" for g in c.store.grants(task_id)) else bot.network
            return task.status == Status.RUNNING, network
        return current

    async def call(operation):
        try:
            return await operation
        except BrowserError as e:
            raise HTTPException(409, str(e)) from None

    @v1.get("/tasks/{task_id}/browser")
    async def state(task_id: str):
        return await call(browser.control_state(task_id, policy(task_id)))

    @v1.post("/tasks/{task_id}/browser/take")
    async def take(task_id: str, body: SessionRef):
        return await call(browser.take_control(task_id, body.generation, policy(task_id)))

    @v1.post("/tasks/{task_id}/browser/renew")
    async def renew(task_id: str, body: LeaseRef):
        await call(browser.renew_control(task_id, body.generation, body.lease_generation, body.owner, policy(task_id)))
        return {"ok": True}

    @v1.post("/tasks/{task_id}/browser/release")
    async def release(task_id: str, body: LeaseRef):
        await call(browser.release_control(task_id, body.generation, body.lease_generation, body.owner, policy(task_id)))
        return {"ok": True}

    @v1.post("/tasks/{task_id}/browser/input")
    async def input_event(task_id: str, body: BrowserInput):
        await call(browser.control_input(task_id, body.generation, body.lease_generation, body.owner,
                                         policy(task_id), body.event))
        return {"ok": True}

    return v1
