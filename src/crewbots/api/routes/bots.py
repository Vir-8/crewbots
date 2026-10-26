"""Bots: the crew's list, each bot's role card and profile, adding one, and the models they can run on."""

import contextlib
from typing import Any

from fastapi import APIRouter, HTTPException

from crewbots import cards, mcp_catalog
from crewbots import models as models_module
from crewbots.api.context import Context
from crewbots.api.schemas import BotOut, CardBody, CardOut, ModelsOut, NewBot, ProfileOut


def routes(c: Context) -> APIRouter:
    runner, store = c.runner, c.store
    reload_crew = c.reload_crew
    v1 = APIRouter()

    @v1.get("/bots", response_model=list[BotOut])
    async def bots():
        return [
            {"name": b.name, "title": b.title, "description": b.description, "backend": b.backend, "model": b.model,
             "access": b.access, "workspace": str(b.workspace) if b.workspace else None,
             "network": list(b.network) if isinstance(b.network, tuple) else b.network, "mcp": sorted(b.mcp),
             "skills": list(b.skills), "can_delegate": b.can_delegate, "path": str(b.path) if b.path else None,
             "display_name": b.display_name, "avatar": b.avatar, "effort": b.effort,
             "context_window": b.context_window, "proactive": b.proactive}
            for b in runner.crew.bots.values()
        ]

    def edited(fields: dict[str, Any]) -> dict[str, Any]:
        if fields.get("avatar") is not None:
            fields["avatar"] = {k: v for k, v in fields["avatar"].items() if v}
        return fields

    @v1.get("/bots/{name}/card", response_model=CardOut)
    async def get_card(name: str):
        try:
            return cards.card(runner.crew.home, name)
        except KeyError:
            raise HTTPException(404, f"no bot named {name}") from None

    @v1.put("/bots/{name}/card", response_model=CardOut)
    async def put_card(name: str, body: CardBody):
        fields = edited(body.model_dump(exclude_unset=True, exclude={"instructions"}))
        if fields.get("mcp") is not None:  # servers defined on the card move to the crew's list, kept by name
            with contextlib.suppress(KeyError, OSError):
                inline = cards.read(cards._bot_path(runner.crew.home, name))[0].get("mcp")
                for server in fields["mcp"]:
                    if isinstance(inline, dict) and server in inline and server not in mcp_catalog.servers(runner.crew.home):
                        mcp_catalog.save(runner.crew.home, server, inline[server])
        try:
            cards.save_card(runner.crew.home, name, fields, body.instructions)
        except KeyError:
            raise HTTPException(404, f"no bot named {name}") from None
        reload_crew()
        return cards.card(runner.crew.home, name)

    @v1.post("/bots", status_code=201, response_model=CardOut)
    async def new_bot(body: NewBot):
        fields = edited(body.model_dump(exclude_unset=True, exclude={"instructions", "name"}))
        cards.save_card(runner.crew.home, body.name, fields, body.instructions or "", create=True)
        reload_crew()
        return cards.card(runner.crew.home, body.name)

    @v1.get("/bots/{bot}/profile", response_model=ProfileOut)
    async def profile(bot: str):
        """What a bot is doing and has done: its threads, newest first, and the files they handed back."""
        if bot not in runner.crew.bots:
            raise HTTPException(404, f"no bot named {bot}")
        threads = store.threads_of(bot)
        outputs = []
        for thread in threads:
            handoff = store.latest_handoff(thread.id)
            outputs += [{"path": p, "task_id": thread.id, "title": thread.title, "at": handoff.at}
                        for p in (handoff.output if handoff else [])]
        chat = runner.chat(bot)
        mine = [chat, *threads, *(s for r in store.rooms() if (s := store.seat_of(r["id"], bot)))]
        return {"bot": bot, "working": any(t.id in runner.running() and t.id not in runner.held() for t in mine),
                "last_active": max((t.updated_at for t in mine), default=None),
                "threads": [t.to_json() for t in threads], "outputs": outputs[:30]}

    @v1.get("/models", response_model=ModelsOut)
    async def list_models(fresh: bool = False):
        """The models each CLI offers, with the reasoning efforts each takes, as the CLIs report them."""
        return await models_module.models(fresh)

    return v1
