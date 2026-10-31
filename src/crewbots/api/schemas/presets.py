"""Presets: the crew's models saved by name."""

from pydantic import BaseModel


class PresetBot(BaseModel):
    backend: str = "claude"
    model: str | None = None  # blank: the CLI's default
    effort: str | None = None

class PresetBody(BaseModel):
    bots: dict[str, PresetBot] | None = None  # by handle; none: the whole crew as it is now

class PresetOut(BaseModel):
    name: str
    bots: dict[str, PresetBot]

class PresetsOut(BaseModel):
    presets: list[PresetOut]  # in the order they were first saved
    in_use: str | None  # the preset the crew matches now, if any
