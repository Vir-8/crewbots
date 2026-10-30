"""Google's official ACP runtime, with its own subscription profile and sign-in."""

import json
import os
from pathlib import Path

from crewbots import paths
from crewbots.backends.acp import Connection
from crewbots.backends.process import JsonlProcess

VERSION = "1.4.5"


def runtime() -> Path:
    return Path(os.environ.get("CREWBOTS_ANTIGRAVITY_ACP") or
                paths.root() / "bin" / "antigravity-acp" / VERSION / "agy_acp_server.par")


def environment() -> dict[str, str]:
    # Subscription authentication stays in the official runtime. Ambient API/gateway credentials must not change billing.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGY_LLM_GATEWAY_", "AGY_GATEWAY_", "GOOGLE_", "GEMINI_"))
           and k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AGY_ACP_FORCE_FILE_STORAGE")}
    return env | {"GEMINI_HOME": str(paths.antigravity()),
                  "ANTIGRAVITY_HARNESS_PATH": str(runtime().with_name("localharness_external"))}


async def connect(cwd: Path, *, login: bool = False) -> tuple[Connection, dict]:
    binary = runtime()
    if not binary.is_file() or not binary.with_name("localharness_external").is_file():
        raise RuntimeError("Install Google's ACP runtime with `crewbots antigravity install`, then run "
                           "`crewbots antigravity login`.")
    if not login:
        try:
            settings = json.loads((paths.antigravity() / "antigravity-acp" / "settings.json").read_text())
        except (OSError, ValueError):
            settings = {}
        if settings.get("auth", {}).get("type") != "oauth-personal":
            raise RuntimeError("Sign in to the official runtime with `crewbots antigravity login`.")
    connection = Connection(await JsonlProcess.start([str(binary)], cwd=cwd, env=environment()))
    try:
        info = await connection.request("initialize", {
            "protocolVersion": 1, "clientInfo": {"name": "crewbots", "version": "0.1.0"},
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        })
        if info.get("agentInfo", {}).get("name") != "antigravity-acp":
            raise RuntimeError("crewbots requires Google's official antigravity-acp runtime")
        version = tuple(int(n) for n in info.get("agentInfo", {}).get("version", "0").split(".")[:3])
        if version < (1, 4, 5):
            raise RuntimeError("Antigravity ACP 1.4.5 or newer is required for session tool isolation")
        return connection, info
    except BaseException:
        await connection.close()
        raise


def model_list(session: dict) -> list[dict]:
    option = next((o for o in session.get("configOptions", []) if o.get("category") == "model" or o.get("id") == "model"), {})
    thinking = next((o for o in session.get("configOptions", []) if o.get("id") == "thinking_level"), {})
    found = []
    for choice in option.get("options", []):
        for item in choice.get("options", [choice]):
            if "value" in item:
                found.append({"id": item["value"], "label": item.get("name", item["value"]),
                              "description": item.get("description", ""),
                              "efforts": [o["value"] for o in thinking.get("options", [])]
                              if item["value"] == option.get("currentValue") else [],
                              "default_effort": (thinking.get("currentValue")
                                                 if item["value"] == option.get("currentValue") else None),
                              "default": item["value"] == option.get("currentValue")})
    return found


async def models() -> list[dict]:
    paths.antigravity().mkdir(parents=True, exist_ok=True)
    connection, _ = await connect(paths.antigravity())
    try:
        session = await connection.request("session/new", {"cwd": str(paths.antigravity()), "mcpServers": [],
            "_meta": {"antigravity": {"enabledTools": []}}})
        found = model_list(session)
        for model in found:
            if not model["default"]:
                selected = await connection.request("session/set_config_option", {
                    "sessionId": session["sessionId"], "configId": "model", "value": model["id"]})
                choice = next((m for m in model_list(selected) if m["id"] == model["id"]), {})
                model.update({k: choice[k] for k in ("efforts", "default_effort") if k in choice})
        return found
    finally:
        await connection.close()
