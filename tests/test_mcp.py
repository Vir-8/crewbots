"""The crew's MCP servers: kept in mcp.json, named on role cards, found in your other apps or the registry."""

import json
import sys
from datetime import UTC, datetime

import pytest
from fakes import FakeBackend
from fastapi.testclient import TestClient

from crewbots import crew, mcp_catalog, paths
from crewbots.api import create_app
from crewbots.backends import Access, McpServer, RunSpec
from crewbots.backends.claude_cli import ClaudeCLI
from crewbots.backends.codex_cli import CodexExec
from crewbots.crew import CrewError
from crewbots.runner import Runner
from crewbots.store import Store

BLENDER = {"command": "uv", "args": ["run", "blender-mcp"], "env": {"BLENDER_MCP_PORT": "9876"}}
UNITY = {"type": "http", "url": "http://127.0.0.1:8080/mcp", "headers": {"Authorization": "Bearer secret"}}


def a_crew(tmp_path, servers=None, card_mcp="[blender, unity]", backend="claude"):
    home = crew.init(tmp_path / "crew")
    (home / "mcp.json").write_text(json.dumps({"mcpServers": servers if servers is not None else
                                                {"blender": BLENDER, "unity": UNITY}}))
    card = home / "bots" / "engineer-1.md"
    text = card.read_text().replace("---\n", f"---\nmcp: {card_mcp}\n", 1)
    card.write_text(text.replace("backend: codex", f"backend: {backend}").replace("backend: claude", f"backend: {backend}"))
    return home


def test_a_role_card_names_the_crews_servers(tmp_path):
    loaded = crew.load(a_crew(tmp_path))

    engineer = loaded.bots["engineer-1"]
    assert engineer.mcp_names == ("blender", "unity")
    assert engineer.mcp["blender"] == McpServer(command="uv", args=["run", "blender-mcp"], env={"BLENDER_MCP_PORT": "9876"})
    assert engineer.mcp["unity"].transport == "http" and engineer.mcp["unity"].url == "http://127.0.0.1:8080/mcp"
    assert loaded.bots["chief"].mcp == {}  # nothing unless its card names it


def test_a_card_naming_a_server_the_crew_hasnt_got_is_refused(tmp_path):
    with pytest.raises(CrewError, match="no MCP servers named \\['gimp'\\]"):
        crew.load(a_crew(tmp_path, card_mcp="[gimp]"))


def test_codex_bots_cant_have_sse_servers(tmp_path):
    old = {"old": {"type": "sse", "url": "http://localhost:9/sse"}}
    with pytest.raises(CrewError, match="Codex can't use SSE"):
        crew.load(a_crew(tmp_path, servers=old, card_mcp="[old]", backend="codex"))


def test_url_servers_reach_both_backends():
    spec = RunSpec(prompt="x", cwd=".", access=Access.WORKSPACE_WRITE,
                   mcp_servers={"unity": McpServer(url="http://127.0.0.1:8080/mcp", headers={"X": "1"}, transport="http")})

    codex = CodexExec(isolate=False).argv(spec)
    assert 'mcp_servers.unity.url="http://127.0.0.1:8080/mcp"' in codex and 'mcp_servers.unity.http_headers={"X" = "1"}' in codex
    claude = ClaudeCLI().argv(spec)
    config = json.loads(claude[claude.index("--mcp-config") + 1])
    assert config["mcpServers"]["unity"] == {"type": "http", "url": "http://127.0.0.1:8080/mcp", "headers": {"X": "1"}}


def test_removing_a_server_takes_it_off_every_card(tmp_path):
    home = a_crew(tmp_path)

    assert mcp_catalog.remove(home, "unity") == ["engineer-1"]

    assert crew.load(home).bots["engineer-1"].mcp_names == ("blender",)
    assert set(mcp_catalog.servers(home)) == {"blender"} and oct((home / "mcp.json").stat().st_mode)[-3:] == "600"


def test_removing_a_server_finds_a_card_by_its_name_not_its_file(tmp_path):
    home = a_crew(tmp_path)
    card = home / "bots" / "engineer-1.md"
    (home / "bots" / "first.md").write_text(card.read_text().replace("---\n", "---\nname: engineer-1\n", 1))
    card.unlink()

    assert mcp_catalog.remove(home, "unity") == ["engineer-1"]

    assert crew.load(home).bots["engineer-1"].mcp_names == ("blender",)


def test_a_server_defined_on_a_card_moves_to_the_crews_list_when_the_card_is_saved(tmp_path):
    home = a_crew(tmp_path, servers={"blender": BLENDER}, card_mcp="{gimp: {command: run-gimp.sh}, web: {command: x}}")
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, crew.load(home), {"fake": FakeBackend()}, worktree_root=tmp_path / "wts")
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765") as client:
        saved = client.put("/v1/bots/engineer-1/card", json={"mcp": ["gimp", "blender"]},
                           headers={"Authorization": "Bearer t"})

        assert saved.json()["mcp"] == ["gimp", "blender"]
        assert mcp_catalog.servers(home) == {"blender": BLENDER, "gimp": {"command": "run-gimp.sh"}}  # web was dropped
        assert runner.crew.bots["engineer-1"].mcp_names == ("gimp", "blender")
    store.close()


def test_your_apps_servers_are_found_once_each(tmp_path, monkeypatch):
    claude = tmp_path / "claude.json"
    claude.write_text(json.dumps({"mcpServers": {"blender": BLENDER, "crewbots": {"command": "x"}},
                                  "projects": {"/u/Lantern": {"mcpServers": {"UnityMCP": UNITY}},
                                               "/u/Gold Hauler": {"mcpServers": {"UnityMCP": UNITY}}}}))
    codex = tmp_path / "config.toml"
    codex.write_text('[mcp_servers.gimp]\ncommand = "run-gimp.sh"\n\n[mcp_servers.off]\ncommand = "x"\nenabled = false\n'
                     '\n[mcp_servers.web]\nurl = "https://example.com/mcp"\nhttp_headers = { "X-Key" = "k" }\n')
    monkeypatch.setattr(paths, "claude_code_config", lambda: claude)
    monkeypatch.setattr(paths, "claude_desktop_config", lambda: tmp_path / "none.json")
    monkeypatch.setattr(paths, "codex_config", lambda: codex)

    found = mcp_catalog.installed()

    assert [(f["source"], f["name"]) for f in found] == [
        ("Claude Code", "blender"), ("Claude Code · Lantern", "UnityMCP"), ("Codex", "gimp"), ("Codex", "web")]
    assert found[-1]["spec"] == {"type": "http", "url": "https://example.com/mcp", "headers": {"X-Key": "k"}}


REGISTRY_SERVER = {
    "name": "io.github.someone/blender", "title": "Blender", "description": "Drive Blender", "version": "1.2.0",
    "packages": [
        {"registryType": "npm", "identifier": "@someone/blender-mcp", "version": "1.2.0", "transport": {"type": "stdio"},
         "runtimeArguments": [{"type": "positional", "value": "-y"}],
         "environmentVariables": [{"name": "BLENDER_PORT", "isRequired": True, "description": "Its port"},
                                  {"name": "API_KEY", "isSecret": True}]},
        {"registryType": "pypi", "identifier": "blender-mcp", "version": "0.4", "transport": {"type": "stdio"},
         "runtimeArguments": [{"type": "named", "name": "--python", "value": "3.12"}],
         "packageArguments": [{"type": "positional", "value": "serve"},
                              {"type": "named", "name": "--scene", "isRequired": True},
                              {"type": "positional", "valueHint": "file_path"}]},
        {"registryType": "npm", "identifier": "x", "transport": {"type": "sse", "url": "http://127.0.0.1:{port}/sse"}},
    ],
    "remotes": [{"type": "streamable-http", "url": "https://mcp.blender.example/mcp",
                 "headers": [{"name": "Authorization", "isSecret": True, "isRequired": True}]}],
}


def test_a_registry_server_becomes_an_entry_the_crew_can_start():
    entry = mcp_catalog._entry(REGISTRY_SERVER)

    assert [o["id"] for o in entry["options"]] == ["package:0", "package:1", "remote:0"]  # not one that opens a port
    added = datetime(2026, 10, 27, 9, 30, tzinfo=UTC)  # what it depends on is held to what was out then
    assert mcp_catalog.from_registry(entry, "package:0", {"BLENDER_PORT": "9876"}, added) == {
        "command": "npx", "args": ["-y", "--before=2026-10-27T09:30:00Z", "@someone/blender-mcp@1.2.0"],
        "env": {"BLENDER_PORT": "9876"}}
    assert mcp_catalog.from_registry(entry, "package:1", {"--scene": "a.blend", "file_path": "b.blend"}, added) == {
        "command": "uvx", "args": ["--python", "3.12", "--exclude-newer", "2026-10-27T09:30:00Z", "blender-mcp==0.4",
                                   "serve", "--scene", "a.blend", "b.blend"]}
    assert mcp_catalog.from_registry(entry, "remote:0", {"Authorization": "Bearer k"}) == {
        "type": "http", "url": "https://mcp.blender.example/mcp", "headers": {"Authorization": "Bearer k"}}
    with pytest.raises(CrewError, match="needs BLENDER_PORT"):
        mcp_catalog.from_registry(entry, "package:0", {})


async def test_a_check_starts_the_server_and_lists_its_tools(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(
        "from mcp.server.mcpserver import MCPServer\n"
        "server = MCPServer('tiny')\n"
        "@server.tool()\n"
        "def render(scene: str) -> str:\n    return scene\n"
        "server.run()\n")

    ok = await mcp_catalog.check(McpServer(command=sys.executable, args=[str(script)]))
    broken = await mcp_catalog.check(McpServer(command="/nonexistent/mcp-server"))

    assert ok == {"ok": True, "tools": ["render"], "error": None}
    assert not broken["ok"] and broken["error"]


def test_the_app_never_sees_a_servers_secrets(tmp_path):
    home = a_crew(tmp_path)
    store = Store(tmp_path / "crew.db")
    runner = Runner(store, crew.load(home), {"fake": FakeBackend()}, worktree_root=tmp_path / "wts")
    auth = {"Authorization": "Bearer t"}
    with TestClient(create_app(runner, "t", 8765), base_url="http://127.0.0.1:8765") as client:
        listed = client.get("/v1/mcp/servers", headers=auth).json()
        assert listed[1] == {"name": "unity", "transport": "http", "command": None, "args": [],
                             "url": "http://127.0.0.1:8080/mcp", "env_keys": [], "header_keys": ["Authorization"],
                             "bots": ["engineer-1"]}
        assert "secret" not in json.dumps(listed)

        changed = client.put("/v1/mcp/servers/unity", headers=auth,
                             json={"transport": "http", "url": "http://127.0.0.1:8090/mcp"}).json()
        assert changed["url"].endswith(":8090/mcp") and changed["header_keys"] == ["Authorization"]  # kept
        assert mcp_catalog.servers(home)["unity"]["headers"] == {"Authorization": "Bearer secret"}

        assert client.delete("/v1/mcp/servers/blender", headers=auth).json() == {"removed": "blender", "bots": ["engineer-1"]}
        assert runner.crew.bots["engineer-1"].mcp_names == ("unity",)
    store.close()
