"""Bots: the crew's list, each bot's role card and profile, adding one, and the models they can run on."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from crewbots import cards, mcp_catalog, pages
from crewbots import models as models_module
from crewbots.api.context import Context
from crewbots.api.schemas import BotOut, CardBody, CardOut, ModelsOut, NewBot, PageBody, PageOut, ProfileOut


def routes(c: Context) -> APIRouter:
    runner, store = c.runner, c.store
    reload_crew, bot_or_404 = c.reload_crew, c.bot_or_404
    v1 = APIRouter()

    @v1.get("/bots", response_model=list[BotOut])
    async def bots():
        return [
            {"name": b.name, "title": b.title, "description": b.description, "backend": b.backend, "model": b.model,
             "access": b.access, "workspace": str(b.workspace) if b.workspace else None, "folder": str(runner.crew.folder_of(b)),
             "network": list(b.network) if isinstance(b.network, tuple) else b.network, "mcp": sorted(b.mcp),
             "skills": list(b.skills), "chief": runner.is_chief(b.name), "coordinator": runner.is_coordinator(b.name),
             "path": str(b.path) if b.path else None, "display_name": b.display_name, "avatar": b.avatar,
             "effort": b.effort, "context_window": b.context_window, "proactive": b.proactive}
            for b in runner.crew.bots.values()
        ]

    def edited(fields: dict[str, Any]) -> dict[str, Any]:
        if fields.get("avatar") is not None:
            fields["avatar"] = {k: v for k, v in fields["avatar"].items() if v}
        return fields

    def pages_folder(bot: str):
        return runner.crew.pages_of(bot_or_404(bot))

    @v1.get("/bots/{bot}/pages", response_model=list[PageOut])
    async def bot_pages(bot: str):
        """The bot's pages, its scratchpad first."""
        return [_page_out(p) for p in pages.pages(pages_folder(bot))]

    @v1.get("/bots/{bot}/pages/{name:path}", response_model=PageOut)
    async def bot_page(bot: str, name: str):
        folder = pages_folder(bot)
        resolved = pages.resolve(folder, name)  # a [[link]] names a page by its own name too
        try:
            return _page_out(next(p for p in pages.pages(folder) if p.name == resolved), text=True)
        except StopIteration:
            raise HTTPException(404, f"no page named {name!r}") from None

    @v1.put("/bots/{bot}/pages/{name:path}", response_model=PageOut)
    async def put_bot_page(bot: str, name: str, body: PageBody):
        """The user writes one of a bot's pages: it sees the change in its next turn."""
        page = pages.write(pages_folder(bot), name, body.text)
        runner.bus.publish({"type": "page", "bot": bot, "page": page.name})
        return _page_out(page, text=True)

    @v1.get("/bots/{name}/card", response_model=CardOut)
    async def get_card(name: str):
        try:
            return cards.card(runner.crew.home, name)
        except KeyError:
            raise HTTPException(404, f"no bot named {name}") from None

    @v1.put("/bots/{name}/card", response_model=CardOut)
    async def put_card(name: str, body: CardBody):
        fields = edited(body.model_dump(mode="json", exclude_unset=True, exclude={"instructions"}))
        if fields.get("mcp") is not None:
            mcp_catalog.move_inline(runner.crew.home, name, fields["mcp"])
        try:
            cards.save_card(runner.crew.home, name, fields, body.instructions)
        except KeyError:
            raise HTTPException(404, f"no bot named {name}") from None
        reload_crew()
        return cards.card(runner.crew.home, name)

    @v1.post("/bots", status_code=201, response_model=CardOut)
    async def new_bot(body: NewBot):
        fields = edited(body.model_dump(mode="json", exclude_unset=True, exclude={"instructions", "name"}))
        cards.save_card(runner.crew.home, body.name, fields, body.instructions or "", create=True)
        reload_crew()
        return cards.card(runner.crew.home, body.name)

    @v1.get("/bots/{bot}/profile", response_model=ProfileOut)
    async def profile(bot: str):
        """What a bot is doing and has done: its threads, newest first, and the files they handed back."""
        bot_or_404(bot)
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
                "threads": [runner.task_json(t) for t in threads], "outputs": outputs[:30]}

    @v1.get("/models", response_model=ModelsOut)
    async def list_models(fresh: bool = False):
        """The models each CLI offers, with the reasoning efforts each takes, as the CLIs report them."""
        return await models_module.models(fresh)

    return v1


def _page_out(page: pages.Page, text: bool = False) -> dict[str, Any]:
    return {"name": page.name, "path": str(page.path), "updated": datetime.fromtimestamp(page.updated).astimezone().isoformat(),
            "text": page.path.read_text(errors="replace") if text else None}
