"""Presets: the crew's models saved by name (`presets.py`)."""

from fastapi import APIRouter, HTTPException

from crewbots import presets
from crewbots.api.context import Context
from crewbots.api.schemas import PresetBody, PresetsOut


def routes(c: Context) -> APIRouter:
    runner = c.runner
    v1 = APIRouter()

    def listing() -> dict:
        saved = presets.load(runner.crew.home)
        return {"presets": [{"name": name, "bots": bots} for name, bots in saved.items()],
                "in_use": presets.in_use(saved, runner.crew)}

    @v1.get("/presets", response_model=PresetsOut)
    async def list_presets():
        """Every preset, and the ones the crew matches now."""
        return listing()

    @v1.put("/presets/{name:path}", response_model=PresetsOut)
    async def save_preset(name: str, body: PresetBody | None = None):
        """Saves these bots' models as a preset, or the whole crew's as they are now when given none."""
        bots = body.model_dump()["bots"] if body and body.bots else presets.now(runner.crew)
        presets.save(runner.crew, name, bots)
        return listing()

    @v1.delete("/presets/{name:path}", response_model=PresetsOut)
    async def delete_preset(name: str):
        try:
            presets.delete(runner.crew.home, name)
        except KeyError:
            raise HTTPException(404, f"no preset named {name!r}") from None
        return listing()

    return v1
