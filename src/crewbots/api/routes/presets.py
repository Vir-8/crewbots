"""Presets: the crew's models saved by name (`presets.py`), and applying one."""

from fastapi import APIRouter, HTTPException

from crewbots import presets
from crewbots.api.context import Context
from crewbots.api.schemas import ModelsChanged, PresetBody, PresetsOut


def routes(c: Context) -> APIRouter:
    runner = c.runner
    v1 = APIRouter()

    def listing() -> dict:
        saved = presets.load(runner.crew.home)
        return {"presets": [{"name": name, "bots": bots} for name, bots in saved.items()],
                "in_use": presets.in_use(saved, runner.crew)}

    @v1.get("/presets", response_model=PresetsOut)
    async def list_presets():
        """Every preset, and the one the crew matches now, if any."""
        return listing()

    @v1.put("/presets/{name}", response_model=PresetsOut)
    async def save_preset(name: str, body: PresetBody | None = None):
        """Saves these bots' models as a preset, or the whole crew's as they are now when given none."""
        bots = body.model_dump()["bots"] if body and body.bots else presets.now(runner.crew)
        presets.save(runner.crew, name, bots)
        return listing()

    @v1.delete("/presets/{name}", response_model=PresetsOut)
    async def delete_preset(name: str):
        try:
            presets.delete(runner.crew.home, name)
        except KeyError:
            raise HTTPException(404, f"no preset named {name!r}") from None
        return listing()

    @v1.post("/presets/{name}/apply", response_model=ModelsChanged)
    async def apply_preset(name: str):
        """Sets the bots the preset names to its models, all or nothing; the ones that are gone are skipped."""
        try:
            bots = presets.load(runner.crew.home)[name]
        except KeyError:
            raise HTTPException(404, f"no preset named {name!r}") from None
        changes, gone = presets.changes(bots, runner.crew)
        return {"changed": await c.edit_models(changes), "skipped": gone}

    return v1
