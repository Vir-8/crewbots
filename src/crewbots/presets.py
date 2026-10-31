"""Presets: the crew's models as they were, by name, to go back to ("Everyday", "Codex is low"). Each is a backend,
model and effort per bot, kept in the crew's folder as presets.json, which you can read and edit too. A fresh crew has
none. The app applies one as a draft of the bots it names, which it sends as one change (`PUT /bots/models`).
"""

import json
from pathlib import Path
from typing import Any

from crewbots import cards
from crewbots.backends import BACKENDS
from crewbots.crew import EFFORTS, Crew, CrewError

FILE = "presets.json"
NAME_LENGTH = 40
KEYS = ("backend", "model", "effort")

Setup = dict[str, str | None]  # a bot's backend, model and effort


def load(home: Path) -> dict[str, dict[str, Setup]]:
    """Every preset, by name, in the order they were first saved."""
    path = home / FILE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except ValueError as e:
        raise CrewError(f"{path}: not readable as JSON ({e})") from None
    if not isinstance(raw, dict) or not all(
            isinstance(bots, dict) and all(isinstance(setup, dict) for setup in bots.values()) for bots in raw.values()):
        raise CrewError(f"{path}: expected {{preset: {{bot: {{backend, model, effort}}}}}}")
    return {name: {bot: _setup(setup) for bot, setup in bots.items()} for name, bots in raw.items()}


def now(crew: Crew) -> dict[str, Setup]:
    """The crew's models as they are."""
    return {b.name: {"backend": b.backend, "model": b.model, "effort": b.effort} for b in crew.bots.values()}


def save(crew: Crew, name: str, bots: dict[str, dict[str, Any]]) -> None:
    """Saves a preset of these bots' setups, or replaces the one of that name."""
    name = name.strip()
    if not name or len(name) > NAME_LENGTH:
        raise CrewError(f"a preset's name is 1 to {NAME_LENGTH} characters")
    if unknown := sorted(set(bots) - set(crew.bots)):
        raise CrewError(f"no bots named {', '.join(unknown)}")
    if not bots:
        raise CrewError("a preset needs at least one bot")
    setups = {bot: _setup(setup) for bot, setup in bots.items()}
    for bot, setup in setups.items():
        if setup["backend"] not in BACKENDS:
            raise CrewError(f"{bot}: backend must be one of {sorted(BACKENDS)}, not {setup['backend']!r}")
        if setup["effort"] is not None and setup["effort"] not in EFFORTS:
            raise CrewError(f"{bot}: effort is one of {list(EFFORTS)}, not {setup['effort']!r}")
    presets = load(crew.home)
    presets[name] = setups
    _write(crew.home, presets)


def delete(home: Path, name: str) -> None:
    """KeyError if there's no preset of that name."""
    presets = load(home)
    del presets[name]
    _write(home, presets)


def in_use(presets: dict[str, dict[str, Setup]], crew: Crew) -> list[str]:
    """Every preset the crew matches now, for the bots it names that are still in the crew."""
    current = now(crew)
    return [name for name, bots in presets.items()
            if (present := [bot for bot in bots if bot in current]) and all(bots[bot] == current[bot] for bot in present)]


def _setup(raw: dict[str, Any]) -> Setup:
    setup = {key: raw.get(key) or None for key in KEYS}
    setup["backend"] = setup["backend"] or "claude"
    return setup


def _write(home: Path, presets: dict[str, dict[str, Setup]]) -> None:
    cards.write(home / FILE, json.dumps(presets, indent=2, ensure_ascii=False) + "\n")
